"""MobileNetV3-small character classifier architecture.

Used ONLY by ``scripts/build_recognition_assets.py`` to load the trained
``state_dict`` and export a TorchScript artifact. The serving app never imports
this module — it loads the scripted model with ``torch.jit.load`` (no class
needed). The definition matches ``get_mobilenetv3_char`` in the training
notebook so the saved weights load cleanly.
"""

import torch.nn as nn
import torchvision.models as models


def build_mobilenetv3_char(num_classes, pretrained=False):
    """Recreate the trained char model head-for-head.

    First conv is patched 3ch -> 1ch (grayscale) and the final classifier layer
    is resized to ``num_classes``, exactly as during training.
    """
    weights = models.MobileNet_V3_Small_Weights.DEFAULT if pretrained else None
    model = models.mobilenet_v3_small(weights=weights)
    orig = model.features[0][0]
    model.features[0][0] = nn.Conv2d(
        1,
        orig.out_channels,
        kernel_size=orig.kernel_size,
        stride=orig.stride,
        padding=orig.padding,
        bias=False,
    )
    model.classifier[3] = nn.Linear(model.classifier[3].in_features, num_classes)
    return model
