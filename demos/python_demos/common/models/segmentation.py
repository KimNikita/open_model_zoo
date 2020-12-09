"""
 Copyright (c) 2020 Intel Corporation

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""

import cv2
import numpy as np

from .model import Model


class SegmentationModel(Model):
    def __init__(self, ie, model_path):
        super().__init__(ie, model_path)

        self.input_blob_name = self.prepare_inputs()
        self.out_blob_name = self.prepare_outputs()

    def prepare_inputs(self):
        input_num = len(self.net.input_info)
        if input_num != 1:
            raise RuntimeError("Demo supports topologies only with 1 input")

        blob_name = next(iter(self.net.input_info))
        blob = self.net.input_info[blob_name]
        blob.precision = "U8"
        blob.layout = "NCHW"

        in_size_vector = blob.input_data.shape
        if len(in_size_vector) == 4 and in_size_vector[1] == 3:
            self.n, self.c, self.h, self.w = in_size_vector
        else:
            raise RuntimeError("3-channel 4-dimensional model's input is expected")

        return blob_name

    def prepare_outputs(self):
        output_num = len(self.net.outputs)
        if output_num != 1:
            raise RuntimeError("Demo supports topologies only with 1 output")

        blob_name = next(iter(self.net.outputs))
        blob = self.net.outputs[blob_name]
        blob.precision = "FP32"

        out_size_vector = blob.shape
        if len(out_size_vector) == 3:
            self.out_channels = 0
            self.out_height = out_size_vector[1]
            self.out_width = out_size_vector[2]
        elif len(out_size_vector) == 4:
            self.out_channels = out_size_vector[1]
            self.out_height = out_size_vector[2]
            self.out_width = out_size_vector[3]
        else:
            raise Exception("Unexpected output blob shape {}. Only 4D and 3D output blobs are supported".format(out_size_vector))

        return blob_name

    def preprocess(self, inputs):
        image = inputs
        resized_image = cv2.resize(image, (self.w, self.h))
        meta = {'original_shape': image.shape,
                'resized_shape': resized_image.shape}
        resized_image = resized_image.transpose((2, 0, 1))
        resized_image = resized_image.reshape((self.n, self.c, self.h, self.w))
        dict_inputs = {self.input_blob_name: resized_image}
        return dict_inputs, meta

    def postprocess(self, outputs, meta):
        predictions = outputs[self.out_blob_name].squeeze()
        input_image_height = meta['original_shape'][0]
        input_image_width = meta['original_shape'][1]

        if self.out_channels < 2: # assume the output is already ArgMax'ed
            result = predictions.astype('uint8')
        else:
            result = np.argmax(predictions, axis=0).astype('uint8')

        result = cv2.resize(result, (input_image_width, input_image_height), 0, 0, interpolation=cv2.INTER_NEAREST)
        return result
