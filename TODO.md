# TODO

## Rotating SSH keys

**Status:** open, no decision yet.

An SSH key cannot be rotated from a bundle at the moment. `access.ssh_authorized_keys_files` only
goes into the Ignition configuration, which runs once at first boot, and `update-app.sh` does not
touch `authorized_keys`. Changing a key now means reinstalling from the USB, or editing the file
by hand over an existing session.

**To decide:** is rotating from a bundle a good idea, or should this stay a manual step?

- **By hand** is simple and easy to verify, but it becomes difficult in a large-scale rotation
  across many nodes.
- **From a bundle** scales, but a wrong key locks you out, and you do not see it happen on the
  node.

**Points to weigh:**

- Ignition stores the keys under the name `flatcar-ignition` in `~/.ssh/authorized_keys.d/`, and
  `update-ssh-keys -u core -a flatcar-ignition <file>` can replace that set. This comes from the
  source of Ignition and `update-ssh-keys`, and has not been tested on a node.
- A bundle runs as root on the node. Whoever can sign bundles already has full access, so this
  gives them no extra rights.
- During a rotation the old and the new key have to exist side by side for a while, so the new
  one can be tested before the old one goes.
- A bundle must refuse an empty or invalid key file.
- Hooks do not run in an os bundle that installs a new Flatcar version. If this uses a hook, it
  only works with an app bundle.
- In the builder it would apply to every deployment. Solved per deployment with a hook, the
  builder does not have to change.
