# -*- coding: utf-8 -*-
#
# Copyright (c) 2017 F5 Networks Inc.
# GNU General Public License v3.0 (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type

from ansible.module_utils._text import to_text
from ansible.module_utils.basic import env_fallback
from ansible.module_utils.connection import exec_command
from ansible.module_utils.network.common.utils import to_list, ComplexList
from ansible.module_utils.six import iteritems
from collections import defaultdict

try:
    from icontrol.exceptions import iControlUnexpectedHTTPError
    HAS_F5SDK = True
except ImportError:
    HAS_F5SDK = False


f5_provider_spec = {
    'server': dict(fallback=(env_fallback, ['F5_SERVER'])),
    'server_port': dict(type='int', default=443, fallback=(env_fallback, ['F5_SERVER_PORT'])),
    'user': dict(fallback=(env_fallback, ['F5_USER', 'ANSIBLE_NET_USERNAME'])),
    'password': dict(no_log=True, fallback=(env_fallback, ['F5_PASSWORD', 'ANSIBLE_NET_PASSWORD'])),
    'ssh_keyfile': dict(fallback=(env_fallback, ['ANSIBLE_NET_SSH_KEYFILE']), type='path'),
    'validate_certs': dict(type='bool', fallback=(env_fallback, ['F5_VALIDATE_CERTS'])),
    'transport': dict(default='rest', choices=['cli', 'rest'])
}

f5_argument_spec = {
    'provider': dict(type='dict', options=f5_provider_spec),
}

f5_top_spec = {
    'server': dict(removed_in_version=2.9, fallback=(env_fallback, ['F5_SERVER'])),
    'user': dict(removed_in_version=2.9, fallback=(env_fallback, ['F5_USER', 'ANSIBLE_NET_USERNAME'])),
    'password': dict(removed_in_version=2.9, no_log=True, fallback=(env_fallback, ['F5_PASSWORD'])),
    'validate_certs': dict(removed_in_version=2.9, type='bool', fallback=(env_fallback, ['F5_VALIDATE_CERTS'])),
    'server_port': dict(removed_in_version=2.9, type='int', default=443, fallback=(env_fallback, ['F5_SERVER_PORT'])),
    'transport': dict(removed_in_version=2.9, choices=['cli', 'rest'])
}
f5_argument_spec.update(f5_top_spec)


def get_provider_argspec():
    return f5_provider_spec


# Fully Qualified name (with the partition)
def fqdn_name(partition, value):
    if value is not None and not value.startswith('/'):
        return '/{0}/{1}'.format(partition, value)
    return value


# Fully Qualified name (with partition) for a list
def fq_list_names(partition, list_names):
    if list_names is None:
        return None
    return map(lambda x: fqdn_name(partition, x), list_names)


def to_commands(module, commands):
    spec = {
        'command': dict(key=True),
        'prompt': dict(),
        'answer': dict()
    }
    transform = ComplexList(spec, module)
    return transform(commands)


def run_commands(module, commands, check_rc=True):
    responses = list()
    commands = to_commands(module, to_list(commands))
    for cmd in commands:
        cmd = module.jsonify(cmd)
        rc, out, err = exec_command(module, cmd)
        if check_rc and rc != 0:
            module.fail_json(msg=to_text(err, errors='surrogate_then_replace'), rc=rc)
        responses.append(to_text(out, errors='surrogate_then_replace'))
    return responses


def cleanup_tokens(client):
    try:
        resource = client.api.shared.authz.tokens_s.token.load(
            name=client.api.icrs.token
        )
        resource.delete()
    except Exception:
        pass


class Noop(object):
    """Represent no-operation required

    This class is used in the Difference engine to specify when an attribute
    has not changed. Difference attributes may return an instance of this
    class as a means to indicate when the attribute has not changed.

    The Noop object allows attributes to be set to None when sending updates
    to the API. `None` is technically a valid value in some cases (it indicates
    that the attribute should be removed from the resource).
    """
    pass


class F5BaseClient(object):
    def __init__(self, *args, **kwargs):
        self.params = kwargs

    @property
    def api(self):
        raise F5ModuleError("Management root must be used from the concrete product classes.")

    def reconnect(self):
        """Attempts to reconnect to a device

        The existing token from a ManagementRoot can become invalid if you,
        for example, upgrade the device (such as is done in the *_software
        module.

        This method can be used to reconnect to a remote device without
        having to re-instantiate the ArgumentSpec and AnsibleF5Client classes
        it will use the same values that were initially provided to those
        classes

        :return:
        :raises iControlUnexpectedHTTPError
        """
        self.api = self.mgmt


class AnsibleF5Parameters(object):
    def __init__(self, *args, **kwargs):
        self._values = defaultdict(lambda: None)
        self._values['__warnings'] = []
        self.client = kwargs.pop('client', None)
        params = kwargs.pop('params', None)
        if params:
            self.update(params=params)

    def update(self, params=None):
        if params:
            for k, v in iteritems(params):
                if self.api_map is not None and k in self.api_map:
                    map_key = self.api_map[k]
                else:
                    map_key = k

                # Handle weird API parameters like `dns.proxy.__iter__` by
                # using a map provided by the module developer
                class_attr = getattr(type(self), map_key, None)
                if isinstance(class_attr, property):
                    # There is a mapped value for the api_map key
                    if class_attr.fset is None:
                        # If the mapped value does not have
                        # an associated setter
                        self._values[map_key] = v
                    else:
                        # The mapped value has a setter
                        setattr(self, map_key, v)
                else:
                    # If the mapped value is not a @property
                    self._values[map_key] = v

    def api_params(self):
        result = {}
        for api_attribute in self.api_attributes:
            if self.api_map is not None and api_attribute in self.api_map:
                result[api_attribute] = getattr(self, self.api_map[api_attribute])
            else:
                result[api_attribute] = getattr(self, api_attribute)
        result = self._filter_params(result)
        return result

    def __getattr__(self, item):
        # Ensures that properties that weren't defined, and therefore stashed
        # in the `_values` dict, will be retrievable.
        return self._values[item]

    @property
    def partition(self):
        if self._values['partition'] is None:
            return 'Common'
        return self._values['partition'].strip('/')

    @partition.setter
    def partition(self, value):
        self._values['partition'] = value

    def _filter_params(self, params):
        return dict((k, v) for k, v in iteritems(params) if v is not None)


class F5ModuleError(Exception):
    pass
