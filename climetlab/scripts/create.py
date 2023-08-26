# (C) Copyright 2023 ECMWF.
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.
#

import os

from climetlab.loaders import HDF5Loader, ZarrLoader
from climetlab.utils.config import LoadersConfig
from climetlab.utils.humanize import list_to_human

from .tools import parse_args


class LoadersCmd:
    @parse_args(
        dataset=(
            "--dataset",
            dict(
                help="Name of the HDF5 dataset to use"
                " (default from config or 'dataset')"
            ),
        ),
        format=(
            "--format",
            dict(
                help="The format of the target storage into which to load the data"
                " (default is inferred from target path extension)"
            ),
        ),
        config=(
            "--config",
            dict(
                help="A yaml file that describes which data to use as input"
                " and how to organise them in the target"
            ),
        ),
        path=(
            "--target",
            dict(
                help="Where to store the data. "
                "Currently only a path to a new ZARR or HDF5 file is supported."
            ),
        ),
        init=(
            "--init",
            dict(
                action="store_true",
                help="Initialise zarr",
            ),
        ),
        parts=(
            "--load-parts",
            dict(nargs="+", help="Part(s) of the data to process"),
        ),
        statistics=(
            "--statistics",
            dict(action="store_true", help="Compute statistics."),
        ),
    )
    def do_create(self, args):
        if args.format is None:
            _, ext = os.path.splitext(args.path)
            args.format = ext[1:]

        def no_callback(*args, **kwargs):
            print(*args, **kwargs)
            return

        if os.environ.get("CLIMETLAB_CREATE_SHELL_CALLBACK"):

            def callback(*msg):
                msg = "\n".join(msg)
                import shlex
                import subprocess
                import traceback

                cmd = os.environ.get("CLIMETLAB_CREATE_SHELL_CALLBACK")
                cmd = cmd.format(msg)
                try:
                    print(f"Running {cmd}")
                    args = shlex.split(cmd)  # shlex honors the quotes
                    subprocess.run(args)
                except Exception as e:
                    print(f"Exception when running {cmd}" + traceback.format_exc())
                    print(e)

            callback("Starting-loader.")
        else:
            callback = no_callback

        LOADERS = dict(
            zarr=ZarrLoader,
            h5=HDF5Loader,
            hdf5=HDF5Loader,
            hdf=HDF5Loader,
        )
        if args.format not in LOADERS:
            lst = list_to_human(list(LOADERS.keys()), "or")
            raise ValueError(f"Invalid format '{args.format}', must be one of {lst}.")

        kwargs = vars(args)
        loader_class = LOADERS[args.format]

        lst = [args.parts, args.statistics, args.init]
        if sum(1 for x in lst if x) != 1:
            raise ValueError(
                "Too many options provided."
                'Must choose exactly one option in "--parts", "--statistics", "--config"'
            )

        if args.init:
            assert args.config
            loader = loader_class.from_config(**kwargs)
            loader.initialise()
            exit()

        if args.parts:
            loader = loader_class.from_zarr(**kwargs)
            loader.load(**kwargs)

        if args.statistics:
            loader = loader_class.from_zarr(**kwargs)
            loader_class.add_statistics()
