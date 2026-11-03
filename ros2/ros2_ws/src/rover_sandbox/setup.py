from setuptools import find_packages, setup

package_name = 'rover_sandbox'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='lemon56789',
    maintainer_email='lemon56789@g.skku.edu',
    description='TODO: Package description',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'mock_encoder = rover_sandbox.mock_encoder:main',
            'encoder_listener = rover_sandbox.encoder_listener:main',
            ],
    },
)
