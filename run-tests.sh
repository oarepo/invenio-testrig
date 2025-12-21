#!/usr/bin/env bash
# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# Invenio-Testrig is free software; you can redistribute it and/or modify
# it under the terms of the MIT License; see LICENSE file for more details.
# Quit on errors
set -o errexit

# Quit on unbound symbols
set -o nounset

# python -m check_manifest is not needed as we use pyproject + uv backend which does not require MANIFEST.in
python -m sphinx.cmd.build -qnNW docs docs/_build/html
python -m sphinx.cmd.build -qnNW -b doctest docs docs/_build/doctest
