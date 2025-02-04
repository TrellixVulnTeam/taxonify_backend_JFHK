import os
import tarfile
import tempfile

from pandas.errors import EmptyDataError
from pymongo.errors import WriteError

from aquascope.webserver.data_access.db import upload
from aquascope.webserver.data_access.storage import blob
from aquascope.webserver.data_access.util import (populate_system_with_items, MissingTsvFileError,
                                                  upload_data_dir_to_dataframe, upload_data_to_items_and_filepaths)


def download_and_extract_upload(upload_id, container_name, download_path, extraction_path, storage_client):
    blob.download_blob(storage_client, container_name, upload_id, download_path)

    with tarfile.open(download_path, "r") as tar:
        def is_within_directory(directory, target):
            
            abs_directory = os.path.abspath(directory)
            abs_target = os.path.abspath(target)
        
            prefix = os.path.commonprefix([abs_directory, abs_target])
            
            return prefix == abs_directory
        
        def safe_extract(tar, path=".", members=None, *, numeric_owner=False):
        
            for member in tar.getmembers():
                member_path = os.path.join(path, member.name)
                if not is_within_directory(path, member_path):
                    raise Exception("Attempted Path Traversal in Tar File")
        
            tar.extractall(path, members, numeric_owner=numeric_owner) 
            
        
        safe_extract(tar, extraction_path)


def extraction_path_to_data_path(extr_path):
    data_dir = os.listdir(extr_path)[0]
    data_dir = os.path.join(extr_path, data_dir)
    return data_dir


def parse_upload_package(upload_id, db, storage_client):
    upload.update_state(db, upload_id, 'processing')

    with tempfile.TemporaryDirectory() as tmpdirname:
        local_filepath = os.path.join(tmpdirname, 'localfile')
        extraction_path = os.path.join(tmpdirname, 'extracted')
        container_name = blob.group_id_to_container_name('upload')

        try:
            download_and_extract_upload(upload_id, container_name, local_filepath, extraction_path, storage_client)
            data_path = extraction_path_to_data_path(extraction_path)
            result = populate_system_with_items(upload_id, data_path, db, storage_client)
        except (tarfile.ReadError, WriteError, MissingTsvFileError,
                FileNotFoundError, OSError, IndexError, EmptyDataError):
            upload.update_state(db, upload_id, 'failed')
            return
        except Exception as error:
            upload.update_state(db, upload_id, 'failed')
            raise error

    upload.update_state(db, upload_id, 'finished', **result)


def upload_package_to_item_filenames(storage_client, upload_id):
    with tempfile.TemporaryDirectory() as tmpdirname:
        local_filepath = os.path.join(tmpdirname, 'localfile')
        extraction_path = os.path.join(tmpdirname, 'extracted')
        container_name = blob.group_id_to_container_name('upload')

        try:
            download_and_extract_upload(upload_id, container_name, local_filepath, extraction_path,
                                        storage_client)
            data_path = extraction_path_to_data_path(extraction_path)
            df = upload_data_dir_to_dataframe(data_path)
            items, _ = upload_data_to_items_and_filepaths(data_path, df, upload_id)
            return [item.filename for item in items]
        except (tarfile.ReadError, MissingTsvFileError, FileNotFoundError, EmptyDataError):
            return []
