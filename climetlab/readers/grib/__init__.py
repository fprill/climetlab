# (C) Copyright 2020 ECMWF.
#
# This software is licensed under the terms of the Apache Licence Version 2.0
# which can be obtained at http://www.apache.org/licenses/LICENSE-2.0.
# In applying this licence, ECMWF does not waive the privileges and immunities
# granted to it by virtue of its status as an intergovernmental organisation
# nor does it submit to any jurisdiction.
#
import fnmatch
import logging
import os

from climetlab.utils import progress_bar, tqdm

LOG = logging.getLogger(__name__)


def reader(source, path, magic=None, deeper_check=False):
    if magic is None or magic[:4] == b"GRIB":
        from .reader import GRIBReader

        return GRIBReader(source, path)


def _index_grib_file(path, path_name=None):
    import eccodes

    def parse_field(h):
        field = dict()

        # TODO: move this out
        if isinstance(path_name, str):
            field["_path"] = path_name
        elif path_name is False:
            pass
        elif path_name is None:
            field["_path"] = path
        else:
            raise ValueError(f"Value of path_name cannot be '{path_name}.'")

        i = eccodes.codes_keys_iterator_new(h, "mars")
        try:
            while eccodes.codes_keys_iterator_next(i):
                name = eccodes.codes_keys_iterator_get_name(i)
                value = eccodes.codes_get_string(h, name)
                field[name] = value

        finally:
            eccodes.codes_keys_iterator_delete(i)

        field["_offset"] = eccodes.codes_get_long(h, "offset")
        field["_length"] = eccodes.codes_get_long(h, "totalLength")

        field["_param_id"] = eccodes.codes_get_string(h, "paramId")
        field["param"] = eccodes.codes_get_string(h, "shortName")
        return field

    size = os.path.getsize(path)
    pbar = progress_bar(desc=f"Parsing {path}", total=size)

    with open(path, "rb") as f:
        old_tell = f.tell()
        h = eccodes.codes_grib_new_from_file(f)

        while h:
            try:
                yield parse_field(h)
            finally:
                eccodes.codes_release(h)

            tell = f.tell()
            pbar.update(tell - old_tell)
            old_tell = tell
            h = eccodes.codes_grib_new_from_file(f)
    pbar.close()


def _index_url(path_name, url):
    import climetlab as cml

    path = cml.load_source("url", url).path
    # TODO: should use download_and_cache
    # path = download_and_cache(url)
    yield from _index_grib_file(path, path_name=path_name)


def _index_path(path):
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for name in files:
                yield from _index_grib_file(os.path.join(root, name))
    else:
        yield from _index_grib_file(path)


class DirectoryParserIterator:
    """This class delays parsing the directory for the list of files
    until the iterator is actually used (calling __iter__)
    """

    def __init__(
        self, directory, relative_paths, ignore=None, followlinks=True, verbose=False
    ):
        if ignore is None:
            ignore = []
        self.ignore = ignore
        self.directory = directory
        self.relative_paths = relative_paths
        self.followlinks = followlinks
        self.verbose = verbose

        self._tasks = None

    def __iter__(self):
        for path in self.tasks:
            for entry in self.process_one_task(path):
                yield entry

    @property
    def tasks(self):
        if self._tasks is not None:
            return self._tasks

        LOG.debug(f"Parsing files in {self.directory}")
        assert os.path.isdir(self.directory)

        tasks = []
        for root, _, files in os.walk(self.directory, followlinks=self.followlinks):
            for name in files:
                path = os.path.join(root, name)
                tasks.append(path)

        if tasks:
            if self.verbose:
                print(f"Found {len(tasks)} files to index.")
        else:
            LOG.error(f"Could not find any files to index in {self.directory}")

        self._tasks = tqdm(tasks)

        return self.tasks

class GribIndexingDirectoryParserIterator(DirectoryParserIterator):
    def process_one_task(self, path ):
            LOG.debug(f"Parsing file {path}")

            if self.relative_paths is True:
                _path = os.path.relpath(path, self.directory)
            elif self.relative_paths is False:
                _path = os.path.abspath(path)
            elif self.relative_paths is None:
                _path = path
            else:
                assert False, self.relative_paths


            from climetlab.readers import reader as main_reader
            r = main_reader(self, path)
            if r is None:
                return
            for field in r.index_content():
                field['_path'] = _path
                yield field

_parse_files = GribIndexingDirectoryParserIterator
