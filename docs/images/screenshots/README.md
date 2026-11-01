# Screenshot assets

Store real PNG captures for this project in this directory. The [autoscaling walkthrough](../../../examples/autoscaling/README.md#screenshot-capture-metadata) contains every planned filename, expected view and display location. Captures are pending; filenames shown in callouts are reserved targets.

## Add a capture

1. Follow the walkthrough's capture sequence using one consistent two-tenant run.
2. Save each PNG using its reserved filename. Crop it so tenant names, statuses and worker associations are legible. Exclude credentials, kubeconfig contents and bootstrap logs.
3. Fill in the walkthrough's capture date, tested versions and run metadata.
4. Replace that image's placeholder callout in the walkthrough and every listed display location with a Markdown image, descriptive alt text and a caption. Use a relative image path from the document being edited.
5. Retain the capture instructions and update the image's status so readers can distinguish completed captures from pending slots.
6. Run `python3 scripts/check_repository.py` and `git diff --check` from the repository root.

For example, after `platform-nodeprovider.png` exists, use the appropriate relative path:

| Document | Image path |
| --- | --- |
| Repository README | `docs/images/screenshots/platform-nodeprovider.png` |
| Installation guide | `images/screenshots/platform-nodeprovider.png` |
| Autoscaling walkthrough | `../../docs/images/screenshots/platform-nodeprovider.png` |

Keep captions specific to the captured state. Record whether a worker is provisioning, Ready or retired; pair Platform views with Mac VM inventory for deletion claims. A later capture run must not be described as an image of an earlier validation run.
