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

import os
import os.path
import re
import shlex
import logging as log

from targetd.utils import invoke


class Export(object):

    SECURE = 0x00000001
    RW = 0x00000002
    RO = 0x00000004
    SYNC = 0x00000008
    ASYNC = 0x00000010
    NO_WDELAY = 0x00000020
    NOHIDE = 0x00000040
    CROSSMNT = 0x00000080
    NO_SUBTREE_CHECK = 0x00000100
    INSECURE_LOCKS = 0x00000200
    ROOT_SQUASH = 0x00000400
    NO_ROOT_SQUASH = 0x00000800
    ALL_SQUASH = 0x00001000
    WDELAY = 0x00002000
    HIDE = 0x00004000
    INSECURE = 0x00008000
    NO_ALL_SQUASH = 0x00010000

    _conflicting = (((RW | RO), "Both RO & RW set"),
                    ((INSECURE | SECURE), "Both INSECURE & SECURE set"),
                    ((SYNC | ASYNC), "Both SYNC & ASYNC set"),
                    ((HIDE | NOHIDE), "Both HIDE & NOHIDE set"),
                    ((WDELAY | NO_WDELAY), "Both WDELAY & NO_WDELAY set"),
                    ((ROOT_SQUASH | NO_ROOT_SQUASH),
                     "Only one option of ROOT_SQUASH, NO_ROOT_SQUASH, "
                     "can be specified")
                    )

    bool_option = {
        "secure": SECURE,
        "rw": RW,
        "ro": RO,
        "sync": SYNC,
        "async": ASYNC,
        "no_wdelay": NO_WDELAY,
        "nohide": NOHIDE,
        "crossmnt": CROSSMNT,
        "no_subtree_check": NO_SUBTREE_CHECK,
        "insecure_locks": INSECURE_LOCKS,
        "root_squash": ROOT_SQUASH,
        "all_squash": ALL_SQUASH,
        "wdelay": WDELAY,
        "hide": HIDE,
        "insecure": INSECURE,
        "no_root_squash": NO_ROOT_SQUASH,
        "no_all_squash": NO_ALL_SQUASH
    }

    key_pair = dict(
        mountpoint=str,
        mp=str,
        fsid=None,
        refer=str,
        replicas=str,
        anonuid=int,
        anongid=int,
        sec=str)

    export_regex = r"([\/a-zA-Z0-9\.\-_]+)[\s]+(.+)\((.+)\)"
    octal_nums_regex = r"""\\([0-7][0-7][0-7])"""

    @staticmethod
    def _validate_options(options):
        for e in Export._conflicting:
            if (options & (e[0])) == e[0]:
                raise ValueError(e[1])
        return options

    @staticmethod
    def _validate_key_pairs(kp):
        if kp:
            if isinstance(kp, dict):
                for k, v in kp.items():
                    if k not in Export.key_pair:
                        raise ValueError('option %s not valid' % k)

                return kp
            else:
                raise ValueError('key_value_options domain is None or dict')
        else:
            return {}

    def __init__(self, host, path, bit_wise_options=0, key_value_options=None):

        if host == '<world>':
            self.host = '*'
        else:
            self.host = host
        self.path = path
        self.options = Export._validate_options(bit_wise_options)
        self.key_value_options = Export._validate_key_pairs(key_value_options)

    @staticmethod
    def _parse_opt(options_string):
        bits = 0
        pairs = {}

        if len(options_string):
            options = options_string.split(',')
            for o in options:
                if '=' in o:
                    # We have a key=value
                    key, value = o.split('=')
                    pairs[key] = value
                else:
                    bits |= Export.bool_option[o]

        return bits, pairs

    @staticmethod
    def _override(combined, test, opt_a, opt_b):
        if test & opt_a:
            combined &= ~opt_b

        if test & opt_b:
            combined &= ~opt_a
        return combined

    @staticmethod
    def parse_opt(global_options, specific_options=None):
        gbit, gpairs = Export._parse_opt(global_options)

        if specific_options is None:
            return gbit, gpairs

        sbit, spairs = Export._parse_opt(specific_options)

        Export._validate_options(gbit)
        Export._validate_options(sbit)

        # Remove global options which are overridden by specific
        culled = gbit | sbit
        culled = Export._override(culled, sbit, Export.RO, Export.RW)
        culled = Export._override(culled, sbit, Export.INSECURE, Export.SECURE)
        culled = Export._override(culled, sbit, Export.SYNC, Export.ASYNC)
        culled = Export._override(culled, sbit, Export.HIDE, Export.NOHIDE)
        culled = Export._override(culled, sbit, Export.WDELAY, Export.NO_WDELAY)
        culled = Export._override(culled, sbit,
                                  Export.ROOT_SQUASH,
                                  Export.NO_ROOT_SQUASH)

        gpairs.update(spairs)
        return culled, gpairs

    @staticmethod
    def parse_export(tokens):
        rc = []

        try:
            global_options = ''
            options = ''
            if len(tokens) >= 1:
                path = tokens[0]

                if len(tokens) > 1:
                    for t in tokens[1:]:

                        # Handle global options
                        if t[0] == '-' and not global_options:
                            global_options = t[1:]
                            continue

                        # Check for a host or a host with an options group
                        if '(' and ')' in t:
                            if t[0] != '(':
                                host, options = t[:-1].split('(')
                            else:
                                host = '*'
                                options = t[1:-1]
                        else:
                            host = t

                        rc.append(
                            Export(host, path,
                                   *Export.parse_opt(global_options, options)))
                else:
                    rc.append(Export('*', path))

        except Exception as e:
            log.error("parse_export: %s" % str(e))
            return None

        return rc

    @staticmethod
    def parse_exports_file(f):
        rc = []

        with open(f, "r") as e_f:
            for line in e_f:
                exp = Export.parse_export(
                    shlex.split(Export._chr_encode(line), '#'))
                if exp:
                    rc.extend(exp)

        return rc

    @staticmethod
    def parse_exportfs_output(export_text):
        rc = []
        pattern = re.compile(Export.export_regex)

        for m in re.finditer(pattern, export_text):
            rc.append(
                Export(m.group(2), m.group(1), *Export.parse_opt(m.group(3))))
        return rc

    def options_list(self):
        rc = []
        for k, v in self.bool_option.items():
            if self.options & v:
                rc.append(k)

        for k, v in self.key_value_options.items():
            rc.append('%s=%s' % (k, v))

        return rc

    def options_string(self):
        return ','.join(self.options_list())

    @staticmethod
    def _double_quote_space(s):
        if ' ' in s:
            return '"%s"' % s
        return s

    def __repr__(self):
        return "%s %s(%s)" % (Export._double_quote_space(self.path).ljust(50),
                              self.host, self.options_string())

    def export_file_format(self):
        return "%s %s(%s)\n" % (Export._double_quote_space(self.path),
                                self.host, self.options_string())

    @staticmethod
    def _chr_encode(s):
        # Replace octal values, the export path can contain \nnn in the
        # export name.
        p = re.compile(Export.octal_nums_regex)

        for m in re.finditer(p, s):
            s = s.replace('\\' + m.group(1), chr(int(m.group(1), 8)))

        return s

    def __eq__(self, other):
        return self.path == other.path and self.host == other.host


class Nfs(object):
    """
    Python module for configuring NFS exports
    """
    CMD = 'exportfs'
    EXPORT_FILE = 'targetd.exports'
    EXPORT_FS_CONFIG_DIR = os.getenv("TARGETD_NFS_EXPORT_DIR", '/etc/exports.d')
    MAIN_EXPORT_FILE = os.getenv("TARGETD_NFS_EXPORT", '/etc/exports')

    @staticmethod
    def security_options():
        return "sys", "krb5", "krb5i", "krb5p"

    @staticmethod
    def _save_exports():
        # Remove existing export
        config_file = os.path.join(Nfs.EXPORT_FS_CONFIG_DIR, Nfs.EXPORT_FILE)
        try:
            os.remove(config_file)
        except OSError:
            pass

        # Get exports in /etc/exports
        user_exports = Export.parse_exports_file(Nfs.MAIN_EXPORT_FILE)

        # Recreate all existing exports
        with open(config_file, 'w') as ef:
            for e in Nfs.exports():
                if e not in user_exports:
                    ef.write(e.export_file_format())

    @staticmethod
    def exports():
        """
        Return list of exports
        """
        ec, out, error = invoke([Nfs.CMD, '-v'])
        rc = Export.parse_exportfs_output(out)
        return rc

    @staticmethod
    def export_add(host, path, bit_wise_options, key_value_options):
        """
        Adds a path as an NFS export
        """
        export = Export(host, path, bit_wise_options, key_value_options)
        options = export.options_string()

        cmd = [Nfs.CMD]

        if len(options):
            cmd.extend(['-o', options])

        cmd.extend(['%s:%s' % (host, path)])

        ec, out, err = invoke(cmd, False)
        if ec == 0:
            Nfs._save_exports()
            return None
        elif ec == 22:
            raise ValueError("Invalid option: %s" % err)
        else:
            raise RuntimeError('Unexpected exit code "%s" %s, out= %s' %
                               (str(cmd), str(ec), str(out + ":" + err)))

    @staticmethod
    def export_remove(export):
        ec, out, err = invoke(
            [Nfs.CMD, '-u',
             '%s:%s' % (export.host, export.path)])

        if ec == 0:
            Nfs._save_exports()
