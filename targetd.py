#!/usr/bin/env python

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Copyright 2012, Andy Grover <agrover@redhat.com>
#
# A server that exposes a network interface for the LIO
# kernel target.

import os
import contextlib
import setproctitle
from rtslib import (Target, TPG, NodeACL, FabricModule, BlockStorageObject,
                    NetworkPortal, LUN, MappedLUN, RTSLibError)
import lvm
import json
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from SocketServer import ThreadingMixIn
import socket
from threading import Lock
import yaml
import time

setproctitle.setproctitle("targetd")

config_path = "/etc/target/targetd.yaml"

default_config = dict(
    pool_name = "test",
    user = "foo",
    password = "bar",
    ssl = False,
    target_name = "iqn.2003-01.org.linux-iscsi.%s:targetd" % socket.gethostname()
)

config = {}
if os.path.isfile(config_path):
    config = yaml.load(open(config_path).read())

for key, value in default_config.iteritems():
    if key not in config:
        config[key] = value


# fail early if can't access vg
lvm_handle = lvm.Liblvm()
test_vg = lvm_handle.vgOpen(config['pool_name'], "w")
test_vg.close()
lvm_handle.close()

#
# We can't keep lvm/vg handles open continually since liblvm does weird
# things with signals. Instead, define this context manager that eases
# getting vg in each method and calls close() on vg and lvm objs.
#
@contextlib.contextmanager
def vgopen():
    with contextlib.closing(lvm.Liblvm()) as lvm_handle:
        with contextlib.closing(lvm_handle.vgOpen(config['pool_name'], "w")) as vg:
            yield vg

def volumes(req):
    output = []
    with vgopen() as vg:
        for lv in vg.listLVs():
            output.append(dict(name=lv.getName(), size=lv.getSize(),
                               uuid=lv.getUuid()))
    return output

def create(req, name, size):
    with vgopen() as vg:
        lv = vg.createLvLinear(name, int(size))
        print "LV %s created, size %s" % (name, lv.getSize())

def destroy(req, name):
    fm = FabricModule('iscsi')
    t = Target(fm, config['target_name'])
    tpg = TPG(t, 1)

    if name in (lun.storage_object.name for lun in tpg.luns):
        raise ValueError("Volume '%s' cannot be removed while exported" % name)

    with vgopen() as vg:
        lvs = [lv for lv in vg.listLVs() if lv.getName() == name]
        if not len(lvs) == 1:
            raise LookupError("Volume '%s' not found" % name)
        lvs[0].remove()
        print "LV %s removed" % name

def copy(req, vol_orig, vol_new, timeout=10):
    """
    Create a new volume that is a copy of an existing one.
    If this operation takes longer than the timeout, it will return
    an async completion and report actual status via async_complete().
    """
    with vgopen() as vg:
        orig_lv = [lv for lv in vg.listLVs() if lv.getName() == vol_orig][0]

    copy_size = orig_lv.getSize()
    create(req, vol_new, copy_size)
    try:
        src_path = "/dev/%s/%s" % (config['pool_name'], vol_orig)
        dst_path = "/dev/%s/%s" % (config['pool_name'], vol_new)

        start_time = time.clock()
        with open(src_path, 'rb') as fsrc:
            with open(dst_path, 'wb') as fdst:
                copied = 0
                while copied != copy_size:
                    buf = fsrc.read(1024*1024)
                    if not buf:
                        break
                    fdst.write(buf)
                    copied += len(buf)
                    if time.clock() > (start_time + timeout):
                        req.async_completion()
                        async_status(req, 0, int((float(copied)/copy_size)*100))
        complete_if_async(req, 0)

    except:
        destroy(req, vol_new)
        raise

def export_list(req):
    fm = FabricModule('iscsi')
    t = Target(fm, config['target_name'])
    tpg = TPG(t, 1)

    exports = []
    for na in tpg.node_acls:
        for mlun in na.mapped_luns:
            exports.append(dict(initiator_wwn=na.node_wwn, lun=mlun.mapped_lun,
                                vol=mlun.tpg_lun.storage_object.name))
    return exports

def export_to_initiator(req, vol_name, initiator_wwn, lun):
    # only add new SO if it doesn't exist
    try:
        so = BlockStorageObject(vol_name)
    except RTSLibError:
        so = BlockStorageObject(vol_name, dev="/dev/%s/%s" %
                                (config['pool_name'], vol_name))

    so = BlockStorageObject(vol_name)
    fm = FabricModule('iscsi')
    t = Target(fm, config['target_name'])
    tpg = TPG(t, 1)
    tpg.enable = True
    tpg.set_attribute("authentication", 0)
    np = NetworkPortal(tpg, "0.0.0.0")
    na = NodeACL(tpg, initiator_wwn)

    # only add tpg lun if it doesn't exist
    for tmp_lun in tpg.luns:
        if tmp_lun.storage_object.name == so.name \
                and tmp_lun.storage_object.plugin == 'block':
            tpg_lun = tmp_lun
            break
    else:
        tpg_lun = LUN(tpg, storage_object=so)

    # only add mapped lun if it doesn't exist
    for tmp_mlun in tpg_lun.mapped_luns:
        if tmp_mlun.mapped_lun == lun:
            mapped_lun = tmp_mlun
            break
    else:
        mapped_lun = MappedLUN(na, lun, tpg_lun)

def remove_export(req, vol_name, initiator_wwn):
    fm = FabricModule('iscsi')
    t = Target(fm, config['target_name'])
    tpg = TPG(t, 1)
    na = NodeACL(tpg, initiator_wwn)

    for mlun in na.mapped_luns:
        if mlun.tpg_lun.storage_object.name == vol_name:
            tpg_lun = mlun.tpg_lun
            mlun.delete()
            # be tidy and delete unused tpg lun mappings?
            if not len(list(tpg_lun.mapped_luns)):
                tpg_lun.delete()
            break
    else:
        raise LookupError("Volume '%s' not found in %s exports" %
                          (vol_name, initiator_wwn))

    # TODO: clean up NodeACLs w/o any exports as well?

def pools(req):
    with vgopen() as vg:
        # only support 1 vg for now
        return [dict(name=vg.getName(), size=vg.getSize(), free_size=vg.getFreeSize())]

def async_list(req, clear=False):
    '''
    Return a list of ongoing processes
    To prevent deadlock, this method should never be deferred
    '''
    with long_op_status_lock:
        status_dict = long_op_status.copy()
        if clear:
            long_op_status.clear()
    return status_dict


async_id_lock = Lock()
async_id = 100

def new_async_id():
    global async_id
    with async_id_lock:
        new_id = async_id
        async_id += 1
    return new_id

# Long-running threads update their progress here
long_op_status_lock = Lock()
# async_id -> (code, pct_complete)
long_op_status = dict()

def async_status(req, code, pct_complete=None):
    '''
    update a global array with status of ongoing ops.
    code: 0 if ok
    pct_complete: percent complete, integer 0-100
    '''
    with long_op_status_lock:
        long_op_status[req.async_id] = (code, pct_complete)

def complete_if_async(req, code):
    '''
    Ongoing op is done, remove status if succeeded
    '''
    if req.async_id:
        with long_op_status_lock:
            if not code:
                del long_op_status[req.async_id]


mapping = dict(
    vol_list=volumes,
    vol_create=create,
    vol_destroy=destroy,
    vol_copy=copy,
    export_list=export_list,
    export_create=export_to_initiator,
    export_destroy=remove_export,
    pool_list=pools,
    async_list=async_list,
    )


class TargetHandler(BaseHTTPRequestHandler):

    def do_POST(self):

        self.async_id = None

        # get basic auth string, strip "Basic "
        # TODO: add SSL/TLS, or this is not secure
        try:
            auth64 = self.headers.getheader("Authorization")[6:]
            in_user, in_pass = auth64.decode('base64').split(":")
        except:
            self.send_error(400)
            return

        if in_user != config['user'] or in_pass != config['password']:
            self.send_error(401)
            return

        if not self.path == "/targetrpc":
            self.send_error(404)
            return

        try:
            error = (-1, "jsonrpc error")
            self.id = None
            try:
                content_len = int(self.headers.getheader('content-length'))
                req = json.loads(self.rfile.read(content_len))
            except ValueError:
                # see http://www.jsonrpc.org/specification for errcodes
                errcode = (-32700, "parse error")
                raise

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()

            try:
                version = req['jsonrpc']
                if version != "2.0":
                    raise ValueError
                method = req['method']
                self.id = int(req['id'])
                params = req.get('params', None)
            except (KeyError, ValueError):
                error = (-32600, "not a valid jsonrpc-2.0 request")
                raise

            try:
                if params:
                    result = mapping[method](self, **params)
                else:
                    result = mapping[method](self)
            except KeyError:
                error = (-32601, "method %s not found" % method)
                raise
            except TypeError:
                error = (-32602, "invalid method parameter(s)")
                raise
            except Exception, e:
                error = (-1, "%s: %s" % (type(e).__name__, e))
                raise

            rpcdata = json.dumps(dict(result=result, id=self.id))

        except:
            rpcdata = json.dumps(dict(error=dict(code=error[0], message=error[1]), id=self.id))
            raise

        finally:
            if not self.async_id:
                self.wfile.write(rpcdata)
                self.wfile.close()

    def async_completion(self):
        if not self.async_id:
            self.async_id = new_async_id()
            rpcdata = json.dumps(dict(error=dict(code=self.async_id, message="Async Operation"), id=self.id))
            self.wfile.write(rpcdata)
            self.wfile.close()
            # wfile is buffered, need to do this to flush the response
            self.connection.shutdown(socket.SHUT_WR)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread."""

try:
    server = ThreadedHTTPServer(('', 18700), TargetHandler)
    print "started server"
    server.serve_forever()
except KeyboardInterrupt:
    print "SIGINT received, shutting down"
    server.socket.close()
