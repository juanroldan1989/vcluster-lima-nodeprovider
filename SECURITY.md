# Security policy

## Current support scope

This repository is in its documentation foundation phase and has no runtime release or supported security-fix branch yet. The planned helper will be a privileged local integration capable of creating VMs and executing Platform-generated worker bootstrap commands under the Mac user's account.

The initial scope is trusted local development. An authenticated helper connection does not by itself provide per-tenant authorization or make the service suitable for untrusted public exposure.

## Reporting a vulnerability

If GitHub private vulnerability reporting is enabled for this repository, use [Report a vulnerability](https://github.com/juanroldan1989/vcluster-lima-nodeprovider/security/advisories/new).

Otherwise, use a private contact method on the [maintainer's profile](https://github.com/juanroldan1989). If no private method is available, open an issue asking how to send a private report, without publishing the vulnerability details or credentials. Enabling private vulnerability reporting is a repository setting and is not enabled by adding this file.

Include the affected revision, prerequisites, impact and a minimal reproduction using synthetic data. Do not send working join tokens, API passwords, private keys, kubeconfigs, or unredacted bootstrap logs. There is no guaranteed response time or paid support arrangement.

## Handling local state

The implementation must keep generated credentials, private keys, Terraform/OpenTofu state, ownership records, VM disks and runtime logs out of version control. The initial [.gitignore](.gitignore) establishes exclusions; it is not a secret scanner.

Marking a Terraform value sensitive redacts its display but does not remove it from state. Preserve ownership records while managed workers exist and remove workers through their managed lifecycle before tearing down Platform. Detailed operational instructions will be added with the implementation.
