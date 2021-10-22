# (C) Copyright 2021 ECMWF.
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.
#

import inspect
import logging

LOG = logging.getLogger(__name__)


def add_default_values_and_kwargs(args, kwargs, func):
    assert isinstance(args, (list, tuple))
    assert isinstance(kwargs, dict)

    args = list(args)

    new_kwargs = {}
    new_args = []

    sig = inspect.signature(func)
    bnd = sig.bind(*args, **kwargs)
    parameters_names = list(sig.parameters)

    bnd.apply_defaults()

    new_kwargs.update(bnd.kwargs)

    if parameters_names[0] == "self":
        # func must be method. Store first argument and skip it latter
        LOG.debug('Skipping first parameter because it is called "self"')
        new_args = [args.pop(0)]
        parameters_names.pop(0)

    for name in parameters_names:
        param = sig.parameters[name]

        if param.kind is param.VAR_POSITIONAL:  # param is *args
            new_args = new_args + args
            continue

        if param.kind is param.VAR_KEYWORD:  # param is **kwargs
            new_kwargs.update(bnd.arguments[name])
            continue

        new_kwargs[name] = bnd.arguments[name]

    assert isinstance(new_args, list), new_args
    new_args = tuple(new_args)

    LOG.debug("Fixed input arguments", new_args, new_kwargs)

    return new_args, new_kwargs
