# SPDX-License-Identifier: Apache-2.0
"""Exercise startup conflicts without opening sockets or changing real services."""
import errno
from pathlib import Path
import ssl
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from helper import manage


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.config = {"listen_host": "0.0.0.0", "port": 18443}
        self.args = SimpleNamespace(state_dir=Path('/private/tmp/lima-startup-test'))
        for name, value in (("configuration", self.config), ("service_domain", "gui/501/test-lima"),
                            ("port_in_use", False), ("health", {"status": "ok"})):
            patcher = patch.object(manage, name, return_value=value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        patcher = patch.object(manage.subprocess, "run", return_value=SimpleNamespace(returncode=1))
        self.lookup = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(manage, "run")
        self.command = patcher.start()
        self.addCleanup(patcher.stop)
        self.elapsed = 0
        patcher = patch.object(manage.time, "monotonic", side_effect=lambda: self.elapsed)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(manage.time, "sleep", side_effect=self.advance)
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('builtins.print')
        patcher.start()
        self.addCleanup(patcher.stop)

    def advance(self, seconds):
        self.elapsed += seconds

    def test_delayed_launchd_start_succeeds_in_one_invocation(self):
        def delayed_health(config):
            if self.elapsed < 8:
                raise ConnectionRefusedError()
            return {"status": "ok"}
        self.health.side_effect = delayed_health
        manage.start(self.args)
        self.assertGreaterEqual(self.elapsed, 8)
        self.assertLess(self.elapsed, manage.STARTUP_TIMEOUT)
        self.command.assert_called_once()

    def test_startup_timeout_is_bounded_and_identifies_the_log(self):
        self.health.side_effect = ConnectionRefusedError()
        with self.assertRaisesRegex(RuntimeError, 'within 30 seconds') as caught:
            manage.start(self.args)
        self.assertIn('helper.err', str(caught.exception))
        self.assertEqual(self.elapsed, manage.STARTUP_TIMEOUT)

    def test_other_listener_is_reported_before_registering_or_waiting(self):
        self.port_in_use.return_value = True
        for failure in (ssl.SSLError('certificate verification failed'), ValueError('configuration mismatch')):
            self.health.side_effect = failure
            with self.subTest(failure=type(failure).__name__), self.assertRaisesRegex(RuntimeError, 'TCP port 18443 is already in use') as caught:
                manage.start(self.args)
            self.assertIn('lsof -nP -iTCP:18443 -sTCP:LISTEN', str(caught.exception))
            self.assertIn('complete worker/capacity settings', str(caught.exception))
            self.command.assert_not_called()
            self.sleep.assert_not_called()

    def test_healthy_registered_helper_is_reused(self):
        self.port_in_use.return_value = True
        self.lookup.return_value.returncode = 0
        manage.start(self.args)
        self.health.assert_called_once_with(self.config)
        self.command.assert_not_called()

    def test_foreground_helper_is_not_registered_a_second_time(self):
        self.port_in_use.return_value = True
        with self.assertRaisesRegex(RuntimeError, 'outside its launchd service'):
            manage.start(self.args)
        self.command.assert_not_called()

    def test_free_port_allows_service_registration(self):
        manage.start(self.args)
        self.command.assert_called_once()
        self.assertEqual(self.command.call_args.args[0][:2], ['launchctl', 'bootstrap'])
        self.health.assert_called_once_with(self.config)

    def test_port_claimed_during_startup_is_reported(self):
        self.port_in_use.side_effect = [False, True]
        self.health.side_effect = ConnectionRefusedError()
        with self.assertRaisesRegex(RuntimeError, 'TCP port 18443 is already in use'):
            manage.start(self.args)
        self.command.assert_called_once()


class ListenAddressTests(unittest.TestCase):
    def test_free_address_can_be_used(self):
        with patch.object(manage.socket, 'socket'):
            self.assertFalse(manage.port_in_use({"listen_host": "127.0.0.1", "port": 18443}))

    def test_address_in_use_is_a_conflict(self):
        with patch.object(manage.socket, 'socket') as socket:
            socket.return_value.__enter__.return_value.bind.side_effect = OSError(errno.EADDRINUSE, 'Address already in use')
            self.assertTrue(manage.port_in_use({"listen_host": "127.0.0.1", "port": 18443}))

    def test_bind_permissions_are_not_misreported_as_a_port_conflict(self):
        with patch.object(manage.socket, 'socket') as socket:
            socket.return_value.__enter__.return_value.bind.side_effect = OSError(errno.EACCES, 'Permission denied')
            with self.assertRaisesRegex(RuntimeError, 'Cannot bind the helper listen address: Permission denied'):
                manage.port_in_use({"listen_host": "127.0.0.1", "port": 18443})


class StopTests(unittest.TestCase):
    def setUp(self):
        self.elapsed = 0
        self.args = SimpleNamespace()
        config = {'state_dir': '/private/tmp/lima-stop-test-absent'}
        for name, value in (('configuration', config), ('service_domain', 'gui/501/test-lima')):
            patcher = patch.object(manage, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, value in ((manage.subprocess, 'run'), (manage, 'run'),
                              (manage.os, 'kill'), (manage, 'port_in_use')):
            patcher = patch.object(target, value)
            mock = patcher.start()
            setattr(self, 'lookup' if target is manage.subprocess else 'command' if target is manage and value == 'run' else value, mock)
            self.addCleanup(patcher.stop)
        self.lookup.return_value = SimpleNamespace(returncode=0, stdout=b'  pid = 12345\n')
        for name, effect in (('monotonic', lambda: self.elapsed), ('sleep', self.advance)):
            patcher = patch.object(manage.time, name, side_effect=effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('builtins.print')
        patcher.start()
        self.addCleanup(patcher.stop)

    def advance(self, seconds):
        self.elapsed += seconds

    def test_stop_waits_for_its_service_process_to_exit(self):
        self.kill.side_effect = [None, None, ProcessLookupError()]
        manage.stop(self.args)
        self.assertEqual(self.kill.call_count, 3)
        self.kill.assert_called_with(12345, 0)
        self.assertGreater(self.elapsed, 0)
        self.port_in_use.assert_not_called()

    def test_crash_loop_without_pid_does_not_wait_for_another_listener(self):
        self.lookup.return_value.stdout = b'  state = spawn scheduled\n'
        manage.stop(self.args)
        self.command.assert_called_once_with(['launchctl', 'bootout', 'gui/501/test-lima'])
        self.kill.assert_not_called()
        self.port_in_use.assert_not_called()

    def test_unregistered_service_requires_no_process_wait(self):
        self.lookup.return_value.returncode = 1
        manage.stop(self.args)
        self.command.assert_not_called()
        self.kill.assert_not_called()

    def test_shutdown_wait_is_bounded(self):
        self.kill.return_value = None
        with self.assertRaisesRegex(RuntimeError, 'process 12345 has not exited'):
            manage.stop(self.args)
        self.assertEqual(self.elapsed, manage.STARTUP_TIMEOUT)
