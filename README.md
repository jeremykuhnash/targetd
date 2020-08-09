[![Build Status](https://travis-ci.org/open-iscsi/targetd.svg?branch=master)](https://travis-ci.org/open-iscsi/targetd)

Remote configuration of a LIO-based storage appliance
-----------------------------------------------------
targetd turns Linux into a remotely-configurable storage appliance. It
supports an HTTP/jsonrpc-2.0 interface to let a remote administrator
allocate volumes from an LVM volume group, and export those volumes
over iSCSI.  It also has the ability to create remote file systems and export
those file systems via NFS/CIFS (work in progress).

targetd's sister project is [libStorageManagement](https://github.com/libstorage/libstoragemgmt/),
which allows admins to configure storage arrays (including targetd) in an array-neutral manner.

targetd development
-------------------
targetd is licensed under the GPLv3. Contributions are welcome.
 
 * Mailing list: [targetd-devel](https://lists.fedorahosted.org/mailman/listinfo/targetd-devel)
 * Source repo: [GitHub](https://github.com/open-iscsi/targetd)
 * Bugs: [GitHub](https://github.com/open-iscsi/targetd/issues)
 * Releases: [GitHub](https://github.com/open-iscsi/targetd/releases)

**NOTE: targetd is STORAGE-RELATED software, and may be used to
  remove volumes and file systems without warning from the resources it is
  configured to use. Please take care in its use.**

Getting Started
---------------
targetd has these Python library dependencies:
* [python-rtslib](https://github.com/open-iscsi/rtslib-fb) 2.1.fb42+  (must be fb*)
* [libblockdev](https://github.com/storaged-project/libblockdev)
* `python3-blockdev`
* `libblockdev-lvm-dbus` and `lvm2-dbusd` (to use the DBus API **recommended**) **or** 
* `libblockdev-lvm`  to use the lvm binary API
* [python-setproctitle](https://github.com/dvarrazzo/py-setproctitle)
* [PyYAML](http://pyyaml.org/)

All of these are available in Fedora Rawhide and recent Ubuntu versions.

### Configuring targetd

A configuration file may be placed at `/etc/target/targetd.yaml`, and
is in [YAML](http://www.yaml.org/spec/1.2/spec.html) format. Here's
an example:

    user: "foo" # strings quoted, or not
    password: bar
    ssl: false
    target_name: iqn.2003-01.org.example.mach1:1234

    block_pools: [vg-targetd/thin_pool, vg-targetd-too/thin_pool]
    fs_pools: [/mnt/btrfs]
    
    portal_addresses: ["192.168.0.10"]
    
targetd defaults to using the "vg-targetd/thin_pool" volume group and thin
pool logical volume, and username 'admin'. The admin password does not have a
default -- each installation must set it. Use the portal_addresses parameter to set 
explicit addresses that LIO should direct iSCSI connections to, this is 
useful if you are using a proxy such that LIO cannot correctly detect the
public address (e.g. a Kubernetes service). The default behavior is to listen
on all addresses (0.0.0.0).

Then, in the root of the source directory, do the following as root:
```bash
# export PYTHONPATH=`pwd`
# ./scripts/targetd`
```

client.py is a basic testing script, to get started making API calls.

### Docker

targetd can be run in a Docker container. This requires mounting sensitive host directories 
and granting privileged access in order to set up LVM volumes. Use the following command:

```
docker build -t targetd -f docker/Dockerfile .
docker run --privileged -v /etc/target:/etc/target -v /sys/kernel/config:/sys/kernel/config -v /run/lvm:/run/lvm -v /lib/modules:/lib/modules -v /dev:/dev -p 18700:18700 -p 3260:3260 targetd
``` 

where your config is stored at `/etc/target` on the host machine.
