from setuptools import setup
import os.path

version = '0.5dev'

long_description = '\n\n'.join([
    open('README.txt').read(),
    open(os.path.join('lizard_map', 'USAGE.txt')).read(),
    open('TODO.txt').read(),
    open('CREDITS.txt').read(),
    open('CHANGES.txt').read(),
    ])

install_requires = [
    'Django',
    'django-staticfiles',
    'lizard-ui >= 0.6',
    ],

tests_require = [
    ]

setup(name='lizard-map',
      version=version,
      description="Basic map setup for lizard web sites",
      long_description=long_description,
      # Get strings from http://www.python.org/pypi?%3Aaction=list_classifiers
      classifiers=['Programming Language :: Python',
                   'Framework :: Django',
                   ],
      keywords=[],
      author='Reinout van Rees',
      author_email='reinout.vanrees@nelen-schuurmans.nl',
      url='',
      license='GPL',
      packages=['lizard_map'],
      include_package_data=True,
      zip_safe=False,
      install_requires=install_requires,
      tests_require=tests_require,
      extras_require = {'test': tests_require},
      entry_points={
          'console_scripts': [
          ],
          'lizard_map.layer_method': [
            'shapefile_layer = lizard_map.layers:shapefile_layer',
            ]
          },
      )
