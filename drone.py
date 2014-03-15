#!/usr/bin/env python
from optparse import OptionParser
import ConfigParser
import logging
from termcolor import colored
import json
import subprocess
import re
from threading import Thread
from Queue import Queue
from logging_subprocess import call
import io
import os
from collections import defaultdict
from logutils.queue import QueueListener, QueueHandler


class Drone(object):
    """
    Drone is a threaded heroku free tier manager that emulates the
    heroku commandline tool.
    """
    HEROKU_APP_LIMIT = 100

    def __init__(self, config_location='.', logger=None):
        self.config_parser = ConfigParser.RawConfigParser()
        self.queue = Queue()

        self.config_location = config_location
        self.drones_file_name = self.config_location + '/.drone/drones'

        # populated by self.start()
        self.queen = None
        self.drones = None
        self.drones_deployed = defaultdict()
        self.drone_types = None
        self.drone_limit = None
        self.drone_limit_remaining = None

        if not logger:
            self.logger = logging.getLogger(__name__)
        else:
            self.logger = logger

    def start(self):
        """
        Fires up drone with config + apps
        """
        self.drones = self.get_drones()
        self.queen = self.get_queen()
        self.get_config(self.queen)
        self.drone_types = self.get_drone_types()
        self.count_drones_deployed()
        self.get_drone_limit()
        return None

    def get_drone_types(self):
        """
        Reads heroku Procfile to pick out process names and makes them available
        as drone types
        """
        types = []
        file = open(self.config_location + '/Procfile', 'r')
        for line in file.readlines():
            types.append(line.split(':')[0])
        return types

    def get_queen(self):
        """
        Gets the master app
        """
        try:
            with open(self.drones_file_name, 'r') as f:
                all_bees = json.loads(f.read())
        except IOError:
            raise DroneNotInitiated()

        # every drone needs a queen
        if 'queen' not in all_bees:
            raise DroneNotInitiated()

        return all_bees['queen']

    def get_drones(self):
        """
        Gets the list of heroku apps associated with this instance
        of drone
        """
        try:
            with open(self.drones_file_name, 'r') as f:
                all_bees = json.loads(f.read())
        except IOError:
            raise DroneNotInitiated()

        # every drone needs a queen
        if 'queen' not in all_bees:
            raise DroneNotInitiated()

        return all_bees['drones']

    def get_config(self, queen):
        """
        Grab the heroku config items so we can use them for our drones
        """
        try:
            heroku_config = subprocess.check_output(['heroku', 'config', '--app', queen['name']])
        except OSError:
            raise HerokuNotInstalled()

        if heroku_config[0] == '!':
            raise DroneNotInitiated()

        drone_config_string = '[drone]\n' + '\n'.join(heroku_config.split('\n')[1:])
        self.config_parser.readfp(io.BytesIO(drone_config_string))

        return None

    def get_drone_limit(self):
        """
        This function gets the total number of drones we can deploy
        """
        heroku_apps = subprocess.check_output(['heroku', 'apps'])
        heroku_app_count = len(heroku_apps.split('\n')[1:])
        drone_count = len(self.drones)
        self.drone_limit = self.HEROKU_APP_LIMIT - heroku_app_count + drone_count
        self.drone_limit_remaining = self.HEROKU_APP_LIMIT - heroku_app_count

        self.logger.info(str(self.drone_limit_remaining) + ' drones can be deployed.')

        return self.drone_limit

    def count_drones_deployed(self):
        """
        We count up the drones deployed by drone type
        """
        for typ in self.drone_types:
            self.drones_deployed[typ] = 0
            for drone in self.drones:
                if self.drones[drone]['type'] == typ:
                    self.drones_deployed[typ] += 1

        self.logger.info('Drones currently deployed:')
        for typ in self.drone_types:
            self.logger.info('      ' + typ + ': ' + str(self.drones_deployed[typ]))
        self.logger.info('Total: ' + str(len(self.drones)))

        return self.drones_deployed

    @staticmethod
    def get_app_name(text):
        pattern = re.compile('Creating (.+)\.\.\.')
        match = pattern.match(text)
        return match.group(1)

    def save_drones(self):
        try:
            with open(self.drones_file_name, 'w') as f:
                json.dump(
                        {
                            'queen': {
                                'name': self.queen['name']
                            },
                            'drones': self.drones
                        }, f)
        except IOError:
            raise DroneNotInitiated()
        return None

    def initialize(self, queen):
        """
        Create a new drone instance in the current folder
        """
        if not os.path.exists(self.config_location + '/Procfile'):
            raise MissingHerokuApp()

        if not os.path.exists(self.config_location + '/.drone'):
            os.mkdir(self.config_location + '/.drone')
            if not os.path.exists(self.drones_file_name):
                with open(self.drones_file_name, 'w') as f:
                    json.dump(
                        {
                            'queen': {
                                'name': queen
                            },
                            'drones': {}
                        }, f)
        self.logger.info('Drone initialized')

    def create_drone(self, type, drone_number):
        """
        Create a new drone instance of the type
        """
        if not self.drone_limit_remaining:
            raise HerokuAppLimit()

        drone_name = 'drone' + str(drone_number)
        self.logger.info('  ** creating 1 new drone: ' + drone_name)

        # create app
        response = subprocess.check_output(['heroku', 'apps:create'])

        # get name
        app_name = self.get_app_name(response)
        self.logger.info(drone_name + ' created, with name: ' + colored(app_name, 'yellow', attrs=['bold']))

        # make git remote
        call(['heroku', 'git:remote', '-a', app_name, '-r', drone_name], self.logger,
             stdout_log_level=logging.INFO, stderr_log_level=logging.INFO)

        # add config elements
        for config_name, config_val in self.config_parser.items('drone'):
            self.logger.info('----->adding config: ' + config_name + '=' + config_val)
            call(['heroku', 'config:add', config_name.upper() + '=' + config_val, '--app', app_name], self.logger,
                 stdout_log_level=logging.INFO, stderr_log_level=logging.INFO)

        # deploy
        self.logger.info('deploying ' + drone_name)
        call(['git', 'push', drone_name, 'master'], self.logger,
             stdout_log_level=logging.INFO, stderr_log_level=logging.INFO)

        # scaling
        self.logger.info(drone_name + ' is scaling.')
        call(['heroku', 'ps:scale', type + '=1', '--app', app_name], self.logger,
             stdout_log_level=logging.INFO, stderr_log_level=logging.INFO)

        self.queue.put((drone_name, {'remote': drone_name, 'name': app_name, 'type': type}))
        return drone_name, {'remote': drone_name, 'name': app_name, 'type': type}

    def destroy_drone(self, type):
        """
        Destroy an existing drone instance of the type
        """
        target = None
        drone_name = None
        for drone in self.drones:
            if self.drones[drone]['type'] == type:
                target = self.drones[drone]
                drone_name = drone
                break
        call(['heroku', 'apps:destroy', target['name'], '--confirm', target['name']], self.logger,
             stdout_log_level=logging.INFO, stderr_log_level=logging.INFO)
        call(['git', 'remote', 'remove', target['remote']], self.logger,
             stdout_log_level=logging.INFO, stderr_log_level=logging.INFO)
        self.logger.info(drone_name + ' is destroyed.')

        self.queue.put(drone_name)
        return drone_name

    def update_drone(self, drone_name):
        """
        Updates the drone
        """
        drone = self.drones[drone_name]
        call(['heroku', 'maintenance:on', '--app', drone['name']], self.logger,
             stdout_log_level=logging.INFO, stderr_log_level=logging.INFO)
        self.logger.info(drone_name + ' is in maintenance mode')
        self.logger.info('updating ' + drone_name + '...')
        call(['git', 'push', drone['remote'], 'master'], self.logger,
             stdout_log_level=logging.INFO, stderr_log_level=logging.INFO)
        self.logger.info(drone_name + ' is up to date.')
        call(['heroku', 'maintenance:off', '--app', drone['name']], self.logger,
             stdout_log_level=logging.INFO, stderr_log_level=logging.INFO)
        return None


class DroneNotInitiated(Exception):
    pass


class HerokuNotInstalled(Exception):
    pass


class MissingHerokuApp(Exception):
    pass


class HerokuAppLimit(Exception):
    pass


class UnknownDroneType(Exception):
    pass


def main():
    logging_queue = Queue()
    logger, listener = set_up_loggers(logging_queue)
    listener.start()

    # header
    from pyfiglet import Figlet
    f = Figlet(font='alligator')
    logger.info('')
    for line in f.renderText('D R O N E').split('\n'):
        logger.info(colored(line, 'red', attrs=['bold']))
    logger.info('')
    logger.info('')

    # collect options
    parser = OptionParser()
    parser.add_option('--apps:create', dest='create')
    parser.add_option('--ps:scale', dest='scale')
    parser.add_option('--ps:update', action='store_true', dest='update', default=False)
    opts, args = parser.parse_args()
    logger.info('Starting Drone...')
    logger.info('')

    drone = Drone(logger=logger)

    if opts.create:
        drone.initialize(opts.create)
        return None

    drone.start()

    threads = []

    if opts.scale:
        drone_type = opts.scale.split('=')[0]
        if drone_type not in drone.drone_types:
            raise UnknownDroneType()
        amount = int(opts.scale.split('=')[1])
        scaling_amount = amount - drone.drones_deployed[drone_type]
        create_drones = max(0, scaling_amount)
        destroy_drones = max(0, scaling_amount * -1)

        if create_drones:
            if create_drones == 1:
                logger.info('Creating ' + str(create_drones) + ' drone of type ' + drone_type)
            else:
                logger.info('Creating ' + str(create_drones) + ' drones of type ' + drone_type)

            for i in range(create_drones):
                t = Thread(target=drone.create_drone, args=(drone_type, i+1))
                threads.append(t)
                t.start()
            [t.join() for t in threads]
            while not drone.queue.empty():
                new_drone_name, new_drone_dict = drone.queue.get()
                drone.drones[new_drone_name] = new_drone_dict
                drone.save_drones()

        if destroy_drones:
            if create_drones == 1:
                logger.info('Destroying ' + str(destroy_drones) + ' drone of type ' + drone_type)
            else:
                logger.info('Destroying ' + str(destroy_drones) + ' drones of type ' + drone_type)

            for j in range(destroy_drones):
                destroyed_drone = drone.destroy_drone(drone_type)
                del drone.drones[destroyed_drone]
                drone.save_drones()

    if opts.update:
        logger.info('Updating drones...')
        for dron in drone.drones:
            t = Thread(target=drone.update_drone, args=(dron,))
            threads.append(t)
            t.start()
        [t.join() for t in threads]
        logger.info('Drones up to date...')

    # sign off
    logger.info('')
    logger.info('All tasks complete.  Drone signing off...')
    listener.stop()


def set_up_loggers(queue):
    """
    This is a weird thing to do.  Not the proper use of logging
    but it looks cool for the commandline tool.
    """
    queue_handler = QueueHandler(queue)
    handler = logging.StreamHandler()
    listener = QueueListener(queue, handler)
    logger = logging.getLogger('drone')
    logger.addHandler(queue_handler)
    handler.setLevel(logging.INFO)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        colored('[', 'white', attrs=['bold']) +
        colored('%(asctime)s', 'cyan') +
        colored(']', 'white', attrs=['bold']) + ' ' +
        colored('[', 'white', attrs=['bold']) +
        colored('%(threadName)s', 'green') +
        colored(']', 'white', attrs=['bold']) + ' ' +
        colored('%(message)s', 'white')
    )
    handler.setFormatter(formatter)

    return logger, listener


if __name__ == '__main__':
    main()