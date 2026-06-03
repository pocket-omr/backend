"""OMR name-recognition assets and helpers.

This package bundles the character-recognition pipeline used by the grading
endpoints:

- ``sheet_pipeline_v11`` — OpenCV sheet segmentation (warps a photographed
  answer sheet and writes the ``personal_info`` / ``answers`` region crops).
- ``name_preprocess`` — OpenCV/numpy box detection + per-character crop prep,
  extracted verbatim from the training notebook ``final-name-recognition.ipynb``.
- ``char_model`` — the MobileNetV3-small character classifier architecture,
  needed only to convert the trained ``state_dict`` into a TorchScript artifact.
- ``postprocess`` — field clean-up (look-alike char/digit correction).
- ``fuzzy_match`` — weighted Levenshtein roster matching.
"""
