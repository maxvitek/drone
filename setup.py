from distutils.core import setup

setup(
    name='drone',
    version='0.0.1',
    packages=[''],
    url='https://github.com/maxvitek/drone',
    license='',
    author='maxvitek',
    author_email='',
    description='',
    install_requires=['logging_subprocess'],
    entry_points={
        'console_scripts':
            ['drone = drone:main', ]
    }
)
