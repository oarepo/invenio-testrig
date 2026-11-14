..
    Copyright (C) 2026 CESNET z.s.p.o.

    Invenio-Testrig is free software; you can redistribute it and/or
    modify it under the terms of the MIT License; see LICENSE file for more
    details.

Changes
=======

Unreleased

- ``github``: new ``--non-interactive`` mode for automation. It identifies the dispatched run by
  its name (the ``--name`` value), prints ``run-id:``/``run-url:`` lines, never opens a browser,
  and fails when the workflow file can't be updated.
- Workflow template 1.6.0 sets ``run-name`` from the ``name`` input, so runs are titled by their test
  name. Repositories on an older, unmodified template are updated automatically.
- ``github``: a failed workflow dispatch now exits with an error instead of only printing a warning.
- ``github``: new ``--no-create-repository``: a missing or inaccessible target repository is an error
  instead of being created (for automation with a token limited to one existing repository).

Version 1.0.0.dev0

- Initial public release.
