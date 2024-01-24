# Copyright 2024 Iguazio
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
import os

import pytest

import mlrun.errors
from mlrun.datastore import StreamTarget
from mlrun.datastore.targets import KafkaTarget
from mlrun.feature_store import FeatureSet


class MockGraph:
    def __init__(self):
        self.args = None
        self.kwargs = None

    def add_step(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


# ML-5484, ML-5559
def test_stream_target_path_is_without_run_id():
    os.environ["V3IO_ACCESS_KEY"] = os.environ.get("V3IO_ACCESS_KEY", "placeholder")

    mock_graph = MockGraph()
    path = "container/dir/subdir/"
    url = f"v3io:///{path}"
    stream_target = StreamTarget(name="my-target", path=url)
    assert stream_target.get_target_path() == url
    stream_target.run_id = "123"
    fset = FeatureSet(name="my-featureset")
    stream_target.set_resource(fset)
    stream_target.add_writer_step(mock_graph, None, None, key_columns={})
    # make sure that run ID wasn't added to the path
    assert mock_graph.kwargs.get("stream_path") == path


# ML-5484, ML-5559
def test_kafka_target_path_is_without_run_id():
    mock_graph = MockGraph()
    topic = "my-kafka-topic"
    url = f"v3io:///{topic}"
    kafka_target = KafkaTarget(name="my-target", path=url)
    assert kafka_target.get_target_path() == url
    kafka_target.run_id = "123"
    fset = FeatureSet(name="my-featureset")
    kafka_target.set_resource(fset)
    kafka_target.add_writer_step(mock_graph, None, None, key_columns={})
    # make sure that run ID wasn't added to the topic
    assert mock_graph.kwargs.get("topic") == topic


# ML-5560
def test_stream_target_without_path():
    os.environ["V3IO_ACCESS_KEY"] = os.environ.get("V3IO_ACCESS_KEY", "placeholder")

    mock_graph = MockGraph()
    stream_target = StreamTarget(name="my-target")
    assert stream_target.get_target_path() == ""
    stream_target.run_id = "123"
    fset = FeatureSet(name="my-featureset")
    stream_target.set_resource(fset)
    with pytest.raises(mlrun.errors.MLRunInvalidArgumentError):
        stream_target.add_writer_step(mock_graph, None, None, key_columns={})


# ML-5560
def test_kafka_target_without_path():
    mock_graph = MockGraph()
    kafka_target = KafkaTarget(name="my-target")
    assert kafka_target.get_target_path() == ""
    kafka_target.run_id = "123"
    fset = FeatureSet(name="my-featureset")
    kafka_target.set_resource(fset)
    with pytest.raises(mlrun.errors.MLRunInvalidArgumentError):
        kafka_target.add_writer_step(mock_graph, None, None, key_columns={})
