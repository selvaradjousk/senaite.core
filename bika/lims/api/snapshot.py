# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.CORE.
#
# SENAITE.CORE is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright 2018-2019 by it's authors.
# Some rights reserved, see README and LICENSE.

import json

from bika.lims import _
from bika.lims import api
from bika.lims import logger
from bika.lims.api.security import get_roles
from bika.lims.api.security import get_user_id
from bika.lims.interfaces import IAuditable
from DateTime import DateTime
from persistent.list import PersistentList
from plone.memoize.ram import cache
from senaite.core.supermodel import SuperModel
from zope.annotation.interfaces import IAnnotatable
from zope.annotation.interfaces import IAnnotations
from zope.interface import alsoProvides

SNAPSHOT_STORAGE = "senaite.core.snapshots"


def _objectdata_cache_key(func, obj):
    """Cache Key for object data
    """
    uid = api.get_uid(obj)
    modified = api.get_modification_date(obj).millis()
    review_state = api.get_review_status(obj)
    return "{}-{}-{}".format(uid, review_state, modified)


def supports_snapshots(obj):
    """Checks if the object supports snapshots

    Only objects which can hold an annotation storage can be auditable

    :param obj: Content object
    :returns: True/False
    """
    return IAnnotatable.providedBy(obj)


def get_storage(obj):
    """Get or create the audit log storage for the given object

    :param obj: Content object
    :returns: PersistentList
    """
    annotation = IAnnotations(obj)
    if annotation.get(SNAPSHOT_STORAGE) is None:
        annotation[SNAPSHOT_STORAGE] = PersistentList()
    return annotation[SNAPSHOT_STORAGE]


def get_snapshots(obj):
    """Get all snapshots from the storage

    :param obj: Content object
    :returns: List of snapshot dictionaries
    """
    snapshots = get_storage(obj)
    return map(json.loads, snapshots)


def has_snapshots(obj):
    """Checks if the object has snapshots

    :param obj: Content object
    :returns: True/False
    """
    storage = get_storage(obj)
    return len(storage) > 0


def get_snapshot_count(obj):
    """Returns the number of snapsots

    :param obj: Content object
    :returns: Current snapshots in the storage
    """
    try:
        annotation = IAnnotations(obj)
    except TypeError:
        return 0
    storage = annotation.get(SNAPSHOT_STORAGE, [])
    return len(storage)


def get_version(obj):
    """Returns the version of the object

    NOTE: Object versions start with 0!

    :param obj: Content object
    :returns: Current version of the object or -1
    """
    count = get_snapshot_count(obj)
    if count == 0:
        return -1
    return count - 1


def get_snapshot_by_version(obj, version=0):
    """Get a snapshot by version

    Snapshot versions begin with `0`, because this is the first index of the
    storage, which is a list.

    :param obj: Content object
    :param version: The index position of the snapshot in the storage
    :returns: Snapshot at the given index position
    """
    if version < 0:
        return None
    snapshots = get_snapshots(obj)
    if version > len(snapshots) - 1:
        return None
    return snapshots[version]


def get_snapshot_version(obj, snapshot):
    """Returns the version of the given snapshot

    :param obj: Content object
    :param snapshot: Snapshot dictionary
    :returns: Index where the object is lcated
    """
    snapshots = get_snapshots(obj)
    return snapshots.index(snapshot)


def get_last_snapshot(obj):
    """Get the last snapshot

    :param obj: Content object
    :returns: Last Snapshot or None
    """
    version = get_version(obj)
    return get_snapshot_by_version(obj, version)


def get_snapshot_metadata(snapshot):
    """Returns the snapshot metadata

    :param snapshot: Snapshot dictionary
    :returns: Metadata dictionary of the snapshot
    """
    return snapshot.get("__metadata__", {})


@cache(_objectdata_cache_key)
def get_object_data(obj):
    """Get object schema data

    NOTE: We RAM cache this data because it should only change when the object
    was modified!

    XXX: We need to set at least the modification date when we set fields in
    Ajax Listing when we take a snapshot there!

    :param obj: Content object
    :returns: Dictionary of extracted schema data
    """

    model = SuperModel(obj)
    try:
        data = model.to_dict()
    except Exception as exc:
        logger.error("Failed to get schema data for {}: {}"
                     .format(repr(obj), str(exc)))
        data = {}

    return data


def get_request_data(request=None):
    """Get request header/form data

    :param request: Request object
    :returns: Dictionary of extracted request header/form data
    """

    if request is None:
        # get the request
        request = api.get_request()

    # Happens in the test runner
    if not request:
        return {}

    return {
        "comments": request.form.get("comments", ""),
        "remote_address": request.get_header("REMOTE_ADDR"),
        "user_agent": request.get_header("HTTP_USER_AGENT"),
        "referer": request.get_header("HTTP_REFERER"),
    }


def get_object_metadata(obj, **kw):
    """Get object metadata

    :param obj: Content object
    :returns: Dictionary of extracted object metadata
    """

    # inject metadata of volatile data
    metadata = {
        "actor": get_user_id(),
        "roles": get_roles(),
        "action": "",
        "review_state": api.get_review_status(obj),
        "active": api.is_active(obj),
        "snapshot_created": DateTime().ISO(),
        "modified": api.get_modification_date(obj).ISO(),
        "remote_address": "",
        "user_agent": "",
        "referer": "",
        "comments": "",
    }

    # Update request data
    metadata.update(get_request_data())

    # allow metadata overrides
    metadata.update(kw)

    return metadata


def take_snapshot(obj, store=True, **kw):
    """Takes a snapshot of the passed in object

    :param obj: Content object
    :returns: New snapshot
    """
    logger.debug("📷 Take new snapshot for {}".format(repr(obj)))

    # get the object data
    snapshot = get_object_data(obj)

    # get the metadata
    metadata = get_object_metadata(obj, **kw)

    # store the metadata
    snapshot["__metadata__"] = metadata

    # convert the snapshot to JSON
    data = json.dumps(snapshot)

    # return immediately
    if not store:
        return snapshot

    # get the snapshot storage
    storage = get_storage(obj)

    # store the snapshot data
    storage.append(data)

    # Mark the content as auditable
    alsoProvides(obj, IAuditable)

    return snapshot


def compare_snapshots(snapshot_a, snapshot_b, raw=False):
    """Returns a diff of two given snapshots (dictionaries)

    :param snapshot_a: First snapshot
    :param snapshot_b: Second snapshot
    :param raw: True to compare the raw values, e.g. UIDs
    :returns: Dictionary of field/value pairs that differ
    """
    if not all(map(lambda x: isinstance(x, dict),
                   [snapshot_a, snapshot_b])):
        return {}

    diffs = {}
    for key_a, value_a in snapshot_a.iteritems():
        # skip fieds starting with _ or __
        if key_a.startswith("_"):
            continue
        # get the value of the second snapshot
        value_b = snapshot_b.get(key_a)
        # get the diff between the two values
        diff = diff_values(value_a, value_b, raw=raw)
        if diff is not None:
            diffs[key_a] = diff
    return diffs


def compare_last_two_snapshots(obj, raw=False):
    """Helper to compare the last two snapshots directly
    """

    if get_snapshot_count(obj) < 2:
        return {}

    version = get_version(obj)

    snap1 = get_snapshot_by_version(obj, version - 1)
    snap2 = get_snapshot_by_version(obj, version)

    return compare_snapshots(snap1, snap2, raw=raw)


def diff_values(value_a, value_b, raw=False):
    """Returns a human-readable diff between two values

    :param value_a: First value to compare
    :param value_b: Second value to compare
    :param raw: True to compare the raw values, e.g. UIDs
    :returns a list of diff tuples
    """

    if not raw:
        value_a = _process_value(value_a)
        value_b = _process_value(value_b)

    # No changes
    if value_a == value_b:
        return None

    diffs = []
    # N.B.: the choice for the tuple data structure is to enable in the future
    # more granular diffs, e.g. the changed values within a dictionary etc.
    diffs.append((value_a, value_b))
    return diffs


def _process_value(value):
    """Convert the value into a human readable diff string
    """
    if not value:
        value = _("Not set")
    # XXX: bad data, e.g. in AS Method field
    elif value == "None":
        value = _("Not set")
    # 0 is detected as the portal UID
    elif value == "0":
        pass
    elif api.is_uid(value):
        value = _get_title_or_id_from_uid(value)
    elif isinstance(value, (dict)):
        value = json.dumps(sorted(value.items()), indent=1)
    elif isinstance(value, (list, tuple)):
        value = sorted(map(_process_value, value))
        value = "; ".join(value)
    elif isinstance(value, unicode):
        value = api.safe_unicode(value).encode("utf8")
    return str(value)


def _get_title_or_id_from_uid(uid):
    """Returns the title or ID from the given UID
    """
    try:
        obj = api.get_object_by_uid(uid)
    except api.APIError:
        return "<Deleted {}>".format(uid)
    title_or_id = api.get_title(obj) or api.get_id(obj)
    return title_or_id