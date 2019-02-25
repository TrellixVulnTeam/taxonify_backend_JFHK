import copy
import os

import dateutil
import pandas as pd
from PIL import Image

from aquascope.webserver.data_access.conversions import (item_id_and_extension_to_blob_name,
                                                         group_id_to_container_name)
from aquascope.webserver.data_access.db import Item, upload
from aquascope.webserver.data_access.db.items import ANNOTABLE_FIELDS, MORPHOMETRIC_FIELDS
from aquascope.webserver.data_access.storage import blob
from aquascope.webserver.data_access.storage.blob import create_container, upload_blob, exists


class MissingTsvFileError(ValueError):
    pass


def populate_db_with_items(items, db):
    items_dicts = [copy.deepcopy(item.get_dict()) for item in items]
    db.items.insert_many(items_dicts)


def populate_db_with_uploads(uploads, db):
    uploads_dicts = [copy.deepcopy(upload.get_dict()) for upload in uploads]
    db.uploads.insert_many(uploads_dicts)


def populate_db_with_users(users, db):
    users_dicts = [copy.deepcopy(user) for user in users]
    db.users.insert_many(users_dicts)


def upload_package_from_stream(filename, stream, db, storage_client):
    container_name = blob.group_id_to_container_name('upload')
    if not blob.exists(storage_client, container_name):
        blob.create_container(storage_client, container_name)

    upload_doc = upload.create(db, filename)
    blob_filename = str(upload_doc.inserted_id)
    blob_meta = dict(filename=filename)
    blob.create_blob_from_stream(storage_client, container_name, blob_filename, stream,
                                 blob_meta)
    upload.update_state(db, blob_filename, 'uploaded')
    return blob_filename


def populate_system_with_items(data_dir, db, storage_client=None):
    features_path = os.path.join(data_dir, 'features.tsv')
    images = os.listdir(data_dir)

    try:
        images.remove('features.tsv')
    except ValueError:
        raise MissingTsvFileError

    converters = {
        'timestamp': lambda x: dateutil.parser.parse(x),
        'url': lambda x: os.path.basename(x),
        **{k: lambda x: float(x) for k in MORPHOMETRIC_FIELDS}
    }
    df = pd.read_csv(features_path, converters=converters, sep='\t')

    for field in ANNOTABLE_FIELDS:
        if field not in df.columns:
            df[field] = None
            df[f'{field}_modified_by'] = None
            df[f'{field}_modification_time'] = None

    items = []
    for item in list(df.to_dict('index').values()):
        image_path = os.path.join(data_dir, os.path.basename(item['url']))
        image = Image.open(image_path)
        width, height = image.size
        items.append(Item.from_tsv_row(item, width, height))

    container_name = group_id_to_container_name(items[0].group_id)
    if storage_client and not exists(storage_client, container_name):
        create_container(storage_client, container_name)

    for item in items:
        result = db.items.insert_one(item.get_dict())
        item._id = result.inserted_id
        blob_name = item_id_and_extension_to_blob_name(item._id, item.extension)

        image_path = os.path.join(data_dir, item.filename)
        blob_meta = dict(filename=item.filename)
        if storage_client:
            upload_blob(storage_client, container_name, blob_name, image_path, blob_meta)
