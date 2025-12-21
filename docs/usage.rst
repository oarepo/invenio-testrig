..
    Copyright (C) 2026 CESNET z.s.p.o.

    Invenio-Testrig is free software; you can redistribute it and/or
    modify it under the terms of the MIT License; see LICENSE file for more
    details.


Usage
=====

Scenario 1: Contributing to Invenio Packages
---------------------------------------------

When contributing to an Invenio package (e.g., ``invenio-records-resources``), you
need to ensure that:

- Tests pass in your modified module
- Tests pass in dependent packages (e.g., ``invenio-rdm-records``)
- Your contribution doesn't break the running repository

Invenio-Testrig will help you with that by running all tests on GitHub.

Setting up and running the tests
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To run tests on GitHub, use the invenio-testrig CLI:

.. code-block:: bash

   invenio-testrig github [org/package#pr_number] ... 

This command will:

- Create a new GitHub repository with the testrig workflow (if it doesn't exist)
- Dispatch the workflow with your specified patches (if any)
- Open a browser window with the workflow run

A ``invenio-testrig-client`` repository will be created in your GitHub account, 
which will be used to run the tests. You can specify a different name and/or organization 
with the ``--target`` option.

If you specify a list of patches in the command, it will automatically start a
workflow run with these patches. If you do not specify any patches, the maintrunk of
all invenio libraries will be tested without any modifications. This is useful to check 
if the maintrunk is in a good state.

If the repository already exists, the command will simply dispatch the workflow
without modifying the repository.

Scenario 2: Testing RDM Repository
------------------------------------

When preparing to release a new version of your repository, you need to ensure
that all frozen dependencies work together correctly and that no tests are broken.
You might also want to test that everything would be working correctly after you
upgrade the dependencies of the repository.

Setup
~~~~~

Create a ``.github/workflows/testrig.yml`` workflow file in the source code of your
InvenioRDM repository:

.. code-block:: yaml

   name: Run on testrig

   on:
       workflow_dispatch:

   permissions:
       contents: write
       id-token: write
       pages: write

   env:
       GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

   jobs:
       verify-patches:
           uses: oarepo/invenio-testrig/.github/workflows/verify-patches.yml@master
           with:
               name: My great repository
               repository: ${{ github.repository }}@${{ github.ref_name }}
               disable-codestyle-checks: true
               python-version: 3.14.2
               # optional stuff
               skip-report: false
               report-repository: ${{ github.repository }}
               report-branch: gh-pages
               report-repository-token: ${{ github.token }}
               ignore-uv-lock: true

Unless you specify ``skip-report``, make sure to have a ``gh-pages`` branch in your
repository where the report will be published. You can create it manually:

.. code-block:: bash

   cd /tmp
   gh repo clone your-org/your-repository
   cd your-repository
   git checkout --orphan gh-pages
   git rm -rf .
   git commit --allow-empty -m "Initialize gh-pages branch"
   git push origin gh-pages

Running the tests
~~~~~~~~~~~~~~~~~

To run the workflow from the command line, use the ``gh`` command:

.. code-block:: bash

   gh workflow run testrig.yml [--ref your-branch]

Use ``--ref`` if you want to test a different branch than ``master`` (the
``testrig.yml`` file must be present on that branch). Alternatively, go to the
GitHub website and run the workflow from the Actions tab.

Advanced configuration
~~~~~~~~~~~~~~~~~~~~~~

In some cases, you may want a more customized configuration — for example, testing
your patch not only on Invenio packages but also on your own extensions. In this
case, copy the ``invenio_testrig/default_config.yaml`` file into your repository
(e.g. as ``customized_testrig_config.yaml``) and specify the config file name in
the workflow's inputs:

.. code-block:: yaml

   verify-patches:
       uses: oarepo/invenio-testrig/.github/workflows/verify-patches.yml@master
       with:
           name: My great repository
           config-file: customized_testrig_config.yaml


How to Reference Patches
-------------------------

The following formats are supported for patches and references to Git repositories:

**Repositories:**

- ``org/package``
- ``org/package@branch``
- ``https://github.com/org/repo``
- ``https://github.com/org/repo/tree/branch-name``

**Pull Requests:**

- ``org/package#pr_number``
- ``org/package@branch[base]``
- ``https://github.com/org/repo/pull/123``

**Pip-installed GitHub references** (for repositories, not pull requests) are also
supported:

- ``https://github.com/inveniosoftware/invenio-records-resources?branch=fix-read-many#c6b973a14802e2a7f73100ab4e32cb0c36bd4672``
- ``https://github.com/inveniosoftware/invenio-swh?rev=v0.13.4#828a3a415cf8e725c369939832b61281c44aec40``

If branches are used, ``invenio-testrig`` will try to find the commits present on
the branch (and not on the unpatched package) and will apply these. If the
application fails, the whole test run will fail.
