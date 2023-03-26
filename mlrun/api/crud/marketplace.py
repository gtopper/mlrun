# Copyright 2018 Iguazio
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import json
from typing import Any, Dict, List, Optional

import mlrun.errors
import mlrun.utils.singleton
from mlrun.api.schemas.marketplace import (
    MarketplaceCatalog,
    MarketplaceItem,
    MarketplaceItemMetadata,
    MarketplaceItemSpec,
    MarketplaceSource,
    ObjectStatus,
)
from mlrun.api.utils.singletons.k8s import get_k8s
from mlrun.config import config
from mlrun.datastore import store_manager

from ..schemas import SecretProviderName
from .secrets import Secrets, SecretsClientType

# Using a complex separator, as it's less likely someone will use it in a real secret name
secret_name_separator = "-__-"


class Marketplace(metaclass=mlrun.utils.singleton.Singleton):
    def __init__(self):
        self._internal_project_name = config.marketplace.k8s_secrets_project_name
        self._catalogs = {}

    @staticmethod
    def _in_k8s():
        k8s_helper = get_k8s()
        return (
            k8s_helper is not None and k8s_helper.is_running_inside_kubernetes_cluster()
        )

    @staticmethod
    def _generate_credentials_secret_key(source, key=""):
        full_key = source + secret_name_separator + key
        return Secrets().generate_client_project_secret_key(
            SecretsClientType.marketplace, full_key
        )

    def add_source(self, source: MarketplaceSource):
        source_name = source.metadata.name
        credentials = source.spec.credentials
        if credentials:
            self._store_source_credentials(source_name, credentials)

    def remove_source(self, source_name):
        self._catalogs.pop(source_name, None)
        if not self._in_k8s():
            return

        source_credentials = self._get_source_credentials(source_name)
        if not source_credentials:
            return
        secrets_to_delete = [
            self._generate_credentials_secret_key(source_name, key)
            for key in source_credentials
        ]
        Secrets().delete_project_secrets(
            self._internal_project_name,
            SecretProviderName.kubernetes,
            secrets_to_delete,
            allow_internal_secrets=True,
        )

    def _store_source_credentials(self, source_name, credentials: dict):
        if not self._in_k8s():
            raise mlrun.errors.MLRunInvalidArgumentError(
                "MLRun is not configured with k8s, marketplace source credentials cannot be stored securely"
            )

        adjusted_credentials = {
            self._generate_credentials_secret_key(source_name, key): value
            for key, value in credentials.items()
        }
        Secrets().store_project_secrets(
            self._internal_project_name,
            mlrun.api.schemas.SecretsData(
                provider=SecretProviderName.kubernetes, secrets=adjusted_credentials
            ),
            allow_internal_secrets=True,
        )

    def _get_source_credentials(self, source_name):
        if not self._in_k8s():
            return {}

        secret_prefix = self._generate_credentials_secret_key(source_name)
        secrets = (
            Secrets()
            .list_project_secrets(
                self._internal_project_name,
                SecretProviderName.kubernetes,
                allow_secrets_from_k8s=True,
                allow_internal_secrets=True,
            )
            .secrets
        )

        source_secrets = {}
        for key, value in secrets.items():
            if key.startswith(secret_prefix):
                source_secrets[key[len(secret_prefix) :]] = value

        return source_secrets

    @staticmethod
    def _transform_catalog_dict_to_schema(
        source: MarketplaceSource, catalog_dict: Dict[str, Any]
    ) -> MarketplaceCatalog:
        """
        Transforms catalog dictionary to MarketplaceCatalog schema
        :param source:          Marketplace source object.
        :param catalog_dict:    raw catalog dict, top level keys are item names,
                                second level keys are version tags ("latest, "1.1.0", ...) and
                                bottom level keys include spec as a dict and all the rest is considered as metadata.
        :return: catalog object
        """
        catalog = MarketplaceCatalog(catalog=[], channel=source.spec.channel)
        # Loop over objects, then over object versions.
        for object_name, object_dict in catalog_dict.items():
            for version_tag, version_dict in object_dict.items():
                object_details_dict = version_dict.copy()
                spec_dict = object_details_dict.pop("spec", {})
                metadata = MarketplaceItemMetadata(
                    tag=version_tag, **object_details_dict
                )
                item_uri = source.get_full_uri(metadata.get_relative_path())
                spec = MarketplaceItemSpec(item_uri=item_uri, **spec_dict)
                item = MarketplaceItem(
                    metadata=metadata,
                    spec=spec,
                    status=ObjectStatus(),
                )
                catalog.catalog.append(item)

        return catalog

    def get_source_catalog(
        self,
        source: MarketplaceSource,
        version: Optional[str] = None,
        tag: Optional[str] = None,
        force_refresh: bool = False,
    ) -> MarketplaceCatalog:
        """
        Getting the catalog object by source.
        If version and/or tag are given, the catalog will be filtered accordingly.

        :param source:          Marketplace source object.
        :param version:         version of items to filter by
        :param tag:             tag of items to filter by
        :param force_refresh:   if True, the catalog will be loaded from source always,
                                otherwise will be pulled from db (if loaded before)
        :return: catalog object
        """
        source_name = source.metadata.name
        if not self._catalogs.get(source_name) or force_refresh:
            url = source.get_catalog_uri()
            credentials = self._get_source_credentials(source_name)
            catalog_data = mlrun.run.get_object(url=url, secrets=credentials)
            catalog_dict = json.loads(catalog_data)
            catalog = self._transform_catalog_dict_to_schema(source, catalog_dict)
            self._catalogs[source_name] = catalog
        else:
            catalog = self._catalogs[source_name]

        result_catalog = MarketplaceCatalog(catalog=[], channel=source.spec.channel)
        for item in catalog.catalog:
            # Because tag and version are optionals,
            # we filter the catalog by one of them with priority to tag
            if (tag is None or item.metadata.tag == tag) and (
                version is None or item.metadata.version == version
            ):
                result_catalog.catalog.append(item)

        return result_catalog

    def get_item(
        self,
        source: MarketplaceSource,
        item_name: str,
        version: Optional[str] = None,
        tag: Optional[str] = None,
        force_refresh: bool = False,
    ) -> MarketplaceItem:
        """
        Retrieve item from source. The item is filtered by tag and version.

        :param source:          Marketplace source object
        :param item_name:       name of the item to retrieve
        :param version:         version of the item
        :param tag:             tag of the item
        :param force_refresh:   if True, the catalog will be loaded from source always,
                                otherwise will be pulled from db (if loaded before)

        :return: marketplace item object

        :raise if the number of collected items from catalog is not exactly one.
        """
        catalog = self.get_source_catalog(source, version, tag, force_refresh)
        items = self._get_catalog_items_filtered_by_name(catalog.catalog, item_name)
        num_items = len(items)

        if not num_items:
            raise mlrun.errors.MLRunNotFoundError(
                f"Item not found. source={item_name}, version={version}"
            )
        if num_items > 1:
            raise mlrun.errors.MLRunInvalidArgumentError(
                "Query resulted in more than 1 catalog items. "
                + f"source={item_name}, version={version}, tag={tag}"
            )
        return items[0]

    @staticmethod
    def _get_catalog_items_filtered_by_name(
        catalog: List[MarketplaceItem],
        item_name: str,
    ) -> List[MarketplaceItem]:
        """
        Retrieve items from catalog filtered by name

        :param catalog:     list of items
        :param item_name:   item name to filter by

        :return:   list of item objects from catalog
        """
        return [item for item in catalog if item.metadata.name == item_name]

    def get_item_object_using_source_credentials(self, source: MarketplaceSource, url):
        credentials = self._get_source_credentials(source.metadata.name)

        if not url.startswith(source.spec.path):
            raise mlrun.errors.MLRunInvalidArgumentError(
                "URL to retrieve must be located in the source filesystem tree"
            )

        if url.endswith("/"):
            obj = store_manager.object(url=url, secrets=credentials)
            listdir = obj.listdir()
            return {
                "listdir": listdir,
            }
        else:
            catalog_data = mlrun.run.get_object(url=url, secrets=credentials)
        return catalog_data
