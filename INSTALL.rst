Installation
============

Prerequisites
-------------

Invenio-Testrig requires **Python 3.14+** and the ``gh`` and ``uv`` commands to
be available on your system:

- Python 3.14+: https://www.python.org/downloads/
- GitHub CLI: https://cli.github.com/
- uv: https://docs.astral.sh/uv/getting-started/installation/

Running Invenio-Testrig
-----------------------

In most cases you do not need to install Invenio-Testrig permanently. Simply run
it via ``uvx``:

.. code-block:: console

   $ uvx invenio-testrig

For convenience, you can add an alias to your shell configuration:

.. code-block:: bash

   # .bashrc
   alias invenio-testrig="uvx invenio-testrig"
