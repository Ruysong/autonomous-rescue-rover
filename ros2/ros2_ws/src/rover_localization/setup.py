"""Install the mock-only B-01 localization package."""

from glob import glob

from setuptools import find_packages, setup


PACKAGE = 'rover_localization'

setup(
    name=PACKAGE,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + PACKAGE]),
        ('share/' + PACKAGE, ['package.xml']),
        ('share/' + PACKAGE + '/config', glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    extras_require={'test': ['pytest']},
    zip_safe=True,
    maintainer='lemon56789',
    maintainer_email='lemon56789@g.skku.edu',
    description='RoverAid B-01 straight-line mock wheel odometry.',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'mock_wheel_odom = rover_localization.mock_wheel_odom:main',
        ],
    },
)
