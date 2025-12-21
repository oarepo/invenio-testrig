..
    Copyright (C) 2026 CESNET z.s.p.o.

    Invenio-Testrig is free software; you can redistribute it and/or
    modify it under the terms of the MIT License; see LICENSE file for more
    details.


Configuration
=============
Invenio-Testrig is configured via a YAML file. The built-in
``invenio_testrig/default_config.yaml`` is used unless you supply your own with
the ``--config`` option (local path or URL).

To customise the configuration, copy the default file into your repository and
point the workflow or CLI at it:

.. code-block:: yaml

   # workflow input
   verify-patches:
       uses: inveniosoftware/invenio-testrig/.github/workflows/verify-patches.yml@master
       with:
           name: My great repository
           config-file: customized_testrig_config.yaml

.. code-block:: console

   # CLI
   $ invenio-testrig setup --config customized_testrig_config.yaml ...

Configuration Reference
-----------------------

seed_repository
~~~~~~~~~~~~~~~

Configuration for the seed InvenioRDM repository used as the foundation for
testing. This repository is cloned and installed (via ``uv sync``) to extract
dependencies and optionally run end-to-end tests.

.. list-table::
   :header-rows: 1
   :widths: 20 10 70

   * - Property
     - Required
     - Description
   * - ``git``
     - yes
     - Git reference to the seed repository in the format ``org/repo@branch``.
       Used to extract dependencies (packages to test) and to run e2e tests.
       Default: ``zenodo/zenodo-rdm@master``
   * - ``e2e``
     - no
     - Git reference to a repository containing an e2e test library /
       configuration. If the seed repository has no ``e2e`` directory, it is
       copied from here. Omit to run unit tests only.

Example:

.. code-block:: yaml

   seed_repository:
     git: zenodo/zenodo-rdm@master
     e2e: oarepo/invenio-e2e@log-xhr

github
~~~~~~

A list of GitHub organisation configurations that define which packages to test
and how. Each entry acts as a filter: packages found in the seed repository's
dependencies are matched against ``include``/``exclude`` patterns and tested
with the specified command.

The same organisation may appear multiple times with different patterns (useful
when one prefix is shared across organisations — the first matching entry wins).

.. list-table::
   :header-rows: 1
   :widths: 20 10 70

   * - Property
     - Required
     - Description
   * - ``org``
     - yes
     - GitHub organisation name. Matched packages are looked up as
       ``org/package-name``.
   * - ``include``
     - no
     - List of regular-expression patterns. Only packages matching at least one
       pattern are tested. Example: ``["invenio-.*"]``
   * - ``exclude``
     - no
     - List of package names to skip even when they match ``include``. Example:
       ``["invenio-xrootd", "invenio-swh"]``
   * - ``test``
     - yes
     - Command (list of strings) used to run the test suite for matching
       packages. Typically ``["./run-tests.sh"]``.
   * - ``extras``
     - no
     - pip extras to install for matching packages. A union is used across all
       packages; unknown extras are silently ignored. Example:
       ``["tests", "opensearch2", "postgresql"]``
   * - ``freeze``
     - no
     - Version constraints (pip format) applied when resolving dependencies.
       Example: ``["setuptools<82.0.0"]``

test_timeout
~~~~~~~~~~~~

Timeout in minutes for each individual package test run. Exceeding the limit
terminates that run.

Default: ``90``

.. code-block:: yaml

   test_timeout: 90

Full Example
------------

The following is the built-in ``default_config.yaml``:

.. code-block:: yaml

   # Optional name for this test configuration run
   name: Invenio testrig

   github:
     - org: "CERNDocumentServer"
       include:
         - "invenio-cern-sync"
       test:
         - ./run-tests.sh
       extras:
         - tests
         - opensearch2
         - postgresql
         - admin
       freeze:
         - setuptools<82.0.0

     - org: "tu-graz-library"
       include:
         - "invenio-curations"
       test:
         - ./run-tests.sh
       extras:
         - tests
         - opensearch2
         - postgresql
         - admin
       freeze:
         - setuptools<82.0.0

     - org: "inveniosoftware"
       include:
         - "invenio-.*"
       exclude:
         - "invenio-xrootd"
         - "invenio-swh"
       test:
         - ./run-tests.sh
       extras:
         - tests
         - opensearch2
         - s3
         - devs
         - s3fs
         - oaipmh
         - rdf
         - sparql
         - postgresql
         - admin
       freeze:
         - setuptools<82.0.0

   seed_repository:
     git: zenodo/zenodo-rdm@master
     e2e:

   test_timeout: 90

Shared Package Prefix Across Organisations
------------------------------------------

When the same package-name prefix is used by multiple GitHub organisations
(e.g. the ``invenio-`` prefix is shared between ``inveniosoftware`` and
``CERNDocumentServer``), list the more-specific organisation **before** the
general one. The first matching entry wins:

.. code-block:: yaml

   github:
     - org: "CERNDocumentServer"
       include:
         - "invenio-cern-sync"
       test:
         - ./run-tests.sh
       extras:
         - tests
         - opensearch2
         - postgresql
         - admin
       freeze:
         - setuptools<82.0.0

     - org: "inveniosoftware"
       include:
         - "invenio-.*"
       # ... rest of inveniosoftware config