Drone:  Manage Heroku Workers
=============================

Drone is a simple Python package for managing
the processes of your heroku applications on the
free tier.

Usage
-----

.. code-block:: bash

    $ python drone.py --apps:create heroku_app_name
    ...
    $ python drone.py --ps:scale process_name=2
    ...
    $ python drone.py --ps:update
    ...
    $ python drone.py --ps:scale process_name=0
    ...