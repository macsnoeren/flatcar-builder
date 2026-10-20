# USB builds for the UPDATE partition

The contents of this directory are copied onto the UPDATE partition of the
maintenance USB. It is bind-mounted into the builder at run time, so a payload
of several gigabytes never enters a Docker layer and can be replaced without
rebuilding the image.

Place one or more USB builds here, each in its own directory whose name starts
with the date it was produced:

```
update/
├── 2026-07-02-my-deployment/
└── 2026-08-10-my-deployment/
```

A USB build is what `build-flatcar.sh <deployment> --mode usb` writes to
`<deployment>/build/usb/`; see [the USB builds](../README.md#the-usb-builds) for
its contents. Copy it rather than linking it, because `build/usb/` is rebuilt
on every usb run:

```sh
cp -r /path/to/my-deployment/build/usb update/2026-10-20-my-deployment
```

The maintenance shell recognises a directory as a build when it holds
`scripts/install-flatcar.sh`, so the naming scheme is free. Builds are listed in
reverse name order, which puts the most recent one first when the name starts
with the date.

This directory may also be left empty. The UPDATE partition is then created at
its minimum size of 1 GiB and grown to fill the stick when the image is written,
so builds can be copied onto a finished stick instead. That is the usual route
once a stick exists, since it avoids rebuilding the image for every new build.

`README.md` is excluded from the copy; everything else in this directory ends up
on the partition.
