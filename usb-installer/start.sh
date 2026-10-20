mkdir -p output cache
docker run --rm --privileged     -v /home/maurice/Projects/jmnl-external-servers/flatcar-usb-installer/output:/output     -v /home/maurice/Projects/jmnl-external-servers/flatcar-usb-installer/update:/build/update:ro     -v /home/maurice/Projects/jmnl-external-servers/flatcar-usb-installer/cache:/build/cache     flatcar-maintenance-builder
