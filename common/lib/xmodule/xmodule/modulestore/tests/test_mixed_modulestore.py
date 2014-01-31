import pymongo
from uuid import uuid4
import unittest
import copy
import ddt
from ddt import data
from mock import patch

from xmodule.tests import DATA_DIR
from xmodule.modulestore import Location, MONGO_MODULESTORE_TYPE, SPLIT_MONGO_MODULESTORE_TYPE
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.modulestore.xml_importer import import_from_xml

# Mixed modulestore depends on django, so we'll manually configure some django settings
# before importing the module
from django.conf import settings
if not settings.configured:
    settings.configure()

from xmodule.modulestore.tests.test_location_mapper import LocMapperSetupSansDjango, loc_mapper
from xmodule.modulestore.mixed import MixedModuleStore

if not settings.configured:
    settings.configure()

# TODO ensure global loc_mapper() goes to the mocked one
# TODO resolve signature discrepancies

@ddt
class TestMixedModuleStore(unittest.TestCase, LocMapperSetupSansDjango):
    """
    Quasi-superclass which tests Location based apps against both split and mongo dbs (Locator and
    Location-based dbs)
    """
    HOST = 'localhost'
    PORT = 27017
    DB = 'test_mongo_%s' % uuid4().hex[:5]
    COLLECTION = 'modulestore'
    FS_ROOT = DATA_DIR
    DEFAULT_CLASS = 'xmodule.raw_module.RawDescriptor'
    RENDER_TEMPLATE = lambda t_n, d, ctx = None, nsp = 'main': ''
    REFERENCE_TYPE = 'Location'

    IMPORT_COURSEID = 'MITx/999/2013_Spring'
    XML_COURSEID1 = 'edX/toy/2012_Fall'
    XML_COURSEID2 = 'edX/simple/2012_Fall'

    modulestore_options = {
        'default_class': DEFAULT_CLASS,
        'fs_root': DATA_DIR,
        'render_template': RENDER_TEMPLATE,
    }
    DOC_STORE_CONFIG = {
        'host': HOST,
        'db': DB,
        'collection': COLLECTION,
    }
    OPTIONS = {
        'mappings': {
            XML_COURSEID1: 'xml',
            XML_COURSEID2: 'xml',
            IMPORT_COURSEID: 'default'
        },
        'reference_type': REFERENCE_TYPE,
        'stores': {
            'xml': {
                'ENGINE': 'xmodule.modulestore.xml.XMLModuleStore',
                'OPTIONS': {
                    'data_dir': DATA_DIR,
                    'default_class': 'xmodule.hidden_module.HiddenDescriptor',
                }
            },
            'direct': {
                'ENGINE': 'xmodule.modulestore.mongo.MongoModuleStore',
                'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                'OPTIONS': modulestore_options
            },
            'draft': {
                'ENGINE': 'xmodule.modulestore.mongo.MongoModuleStore',
                'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                'OPTIONS': modulestore_options
            },
            'split': {
                'ENGINE': 'xmodule.modulestore.split_mongo.SplitMongoModuleStore',
                'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                'OPTIONS': modulestore_options
            }
        }
    }

    def setUp(self):
        """
        Set up the database for testing
        """
        self.connection = pymongo.MongoClient(
            host=self.HOST,
            port=self.PORT,
            tz_aware=True,
        )
        self.connection.drop_database(self.DB)
        
        super(TestMixedModuleStore, self).setUp()
        
        def generate_location(course_id):
            """
            Generate the locations for the given ids
            """
            org, course, run = course_id.split('/')
            return Location('i4x', org, course, 'course', run)

        self.course_locations = {
             course_id: generate_location(course_id)
             for course_id in [self.IMPORT_COURSEID, self.XML_COURSEID1, self.XML_COURSEID2]
        }
        self.fake_location = Location('i4x', 'foo', 'bar', 'vertical', 'baz')
        self.import_chapter_location = self.course_locations[self.IMPORT_COURSEID].replace(
            category='chapter', name='Overview'
        )
        self.xml_chapter_location = self.course_locations[self.XML_COURSEID1].replace(
            category='chapter', name='Overview'
        )
        # get Locators and set up the loc mapper if app is Locator based
        if self.REFERENCE_TYPE != 'Location':
            self.fake_location = loc_mapper().translate_location('foo/bar/2012_Fall', self.fake_location)
            self.import_chapter_location = loc_mapper().translate_location(
                self.IMPORT_COURSEID, self.import_chapter_location
            )
            self.xml_chapter_location = loc_mapper().translate_location(
                self.XML_COURSEID1, self.xml_chapter_location
            )
            self.course_locations = {
                course_id: loc_mapper().translate_location(course_id, locn)
                for course_id, locn in self.course_locations.iteritems()
            }
        patcher = patch('xmodule.modulestore.mixed.loc_mapper', return_value=LocMapperSetupSansDjango.loc_store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        """
        Clear out database after test has completed
        """
        self.connection.drop_database(self.DB)
        super(TestMixedModuleStore, self).tearDown()

    def initdb(self, default):
        """
        Initialize the database and import one test course into it
        """
        # set the default modulestore
        self.options['default'] = self.options[default]
        self.store = MixedModuleStore(**self.options)

        import_from_xml(
            self.store._get_modulestore_for_courseid(self.IMPORT_COURSEID),
            DATA_DIR,
            ['toy'],
            target_location_namespace=Location(
                'i4x',
                self.import_org,
                self.import_course,
                'course',
                self.import_run
            )
        )

        return self.store

    @data('direct', 'split')
    def test_get_modulestore_type(self, default_ms):
        """
        Make sure we get back the store type we expect for given mappings
        """
        self.store = self.initdb(default_ms)
        self.assertEqual(self.store.get_modulestore_type(self.XML_COURSEID1), self.XML_MODULESTORE_TYPE)
        self.assertEqual(self.store.get_modulestore_type(self.XML_COURSEID2), self.XML_MODULESTORE_TYPE)
        mongo_ms_type = MONGO_MODULESTORE_TYPE if default_ms == 'direct' else SPLIT_MONGO_MODULESTORE_TYPE
        self.assertEqual(self.store.get_modulestore_type(self.IMPORT_COURSEID), mongo_ms_type)
        # try an unknown mapping, it should be the 'default' store
        self.assertEqual(self.store.get_modulestore_type('foo/bar/2012_Fall'), mongo_ms_type)

    @data('direct', 'split')
    def test_has_item(self, default_ms):
        self.store = self.initdb(default_ms)
        for course_id, course_locn in self.course_locations.iteritems():
            self.assertTrue(self.store.has_item(course_id, course_locn))

        # try negative cases
        self.assertFalse(self.store.has_item(self.XML_COURSEID1, self.course_locations[self.IMPORT_COURSEID]))
        self.assertFalse(self.store.has_item(self.IMPORT_COURSEID, self.course_locations[self.XML_COURSEID1]))

    @data('direct', 'split')
    def test_get_item(self, default_ms):
        self.store = self.initdb(default_ms)
        with self.assertRaises(NotImplementedError):
            self.store.get_item(self.fake_location)

    @data('direct', 'split')
    def test_get_instance(self, default_ms):
        self.store = self.initdb(default_ms)
        for course_id, course_locn in self.course_locations.iteritems():
            self.assertIsNotNone(self.store.get_instance(course_id, course_locn))

        # try negative cases
        with self.assertRaises(ItemNotFoundError):
            self.store.get_instance(self.XML_COURSEID1, self.course_locations[self.IMPORT_COURSEID])
        with self.assertRaises(ItemNotFoundError):
            self.store.get_instance(self.IMPORT_COURSEID, self.course_locations[self.XML_COURSEID1])

    @data('direct', 'split')
    def test_get_items(self, default_ms):
        self.store = self.initdb(default_ms)
        for course_id, course_locn in self.course_locations.iteritems():
            if hasattr(course_locn, 'as_course_locator'):
                locn = course_locn.as_course_locator()
            else:
                locn = course_locn.replace(org=None, course=None, name=None)
            # NOTE: use get_course if you just want the course. get_items is expensive
            modules = self.store.get_items(locn, course_id, qualifiers={'category': 'course'})
            self.assertEqual(len(modules), 1)
            self.assertEqual(modules[0].location, course_locn)

    @data('direct', 'split')
    def test_update_item(self, default_ms):
        """
        Update should fail for r/o dbs and succeed for r/w ones
        """
        self.store = self.initdb(default_ms)
        # try a r/o db
        course = self.store.get_course(self.course_locations[self.XML_COURSEID1])
        # if following raised, then the test is really a noop, change it
        self.assertFalse(course.show_calculator, "Default changed making test meaningless")
        course.show_calculator = True
        with self.assertRaises(NotImplementedError):
            self.store.update_item(course, None)
        # now do it for a r/w db
        # get_course api's are inconsistent: one takes Locators the other an old style course id
        if hasattr(self.course_locations[self.IMPORT_COURSEID], 'as_course_locator'):
            locn = self.course_locations[self.IMPORT_COURSEID]
        else:
            locn = self.IMPORT_COURSEID
        course = self.store.get_course(locn)
        # if following raised, then the test is really a noop, change it
        self.assertFalse(course.show_calculator, "Default changed making test meaningless")
        course.show_calculator = True
        self.store.update_item(course, None)
        course = self.store.get_course(locn)
        self.assertTrue(course.show_calculator)

    @data('direct', 'split')
    def test_delete_item(self, default_ms):
        """
        Delete should reject on r/o db and work on r/w one
        """
        self.store = self.initdb(default_ms)
        # r/o try deleting the course
        with self.assertRaises(NotImplementedError):
            self.store.delete_item(self.XML_COURSEID1)
        # try deleting the r/w course xblock (a bad pattern, but ok for this test)
        self.store.delete_item(self.course_locations[self.IMPORT_COURSEID])
        # verify it's gone
        # now do it for a r/w db
        self.assertIsNone(
            self.store.get_instance(self.IMPORT_COURSEID, self.course_locations[self.IMPORT_COURSEID])
        )

    @data('direct', 'split')
    def test_get_courses(self, default_ms):
        self.store = self.initdb(default_ms)
        # we should have 3 total courses aggregated
        courses = self.store.get_courses()
        self.assertEqual(len(courses), 3)
        course_ids = [course.location for course in courses]
        self.assertIn(self.course_locations[self.IMPORT_COURSEID], course_ids)
        self.assertIn(self.course_locations[self.XML_COURSEID1], course_ids)
        self.assertIn(self.course_locations[self.XML_COURSEID2], course_ids)

    @data('direct', 'split')
    def test_get_course(self, default_ms):
        self.store = self.initdb(default_ms)
        for course_locn in self.course_locations.itervalues():
            if hasattr(course_locn, 'as_course_locator'):
                locn = course_locn.as_course_locator()
            else:
                locn = course_locn.replace(org=None, course=None, name=None)
            # NOTE: use get_course if you just want the course. get_items is expensive
            modules = self.store.get_course(locn)
            self.assertEqual(len(modules), 1)
            self.assertEqual(modules[0].location, course_locn)

    # pylint: disable=E1101
    @data('direct', 'split')
    def test_get_parent_locations(self, default_ms):
        self.store = self.initdb(default_ms)
        parents = self.store.get_parent_locations(
            self.import_chapter_location,
            self.IMPORT_COURSEID
        )
        self.assertEqual(len(parents), 1)
        self.assertEqual(parents[0], self.course_locations[self.IMPORT_COURSEID])

        parents = self.store.get_parent_locations(
            self.import_chapter_location,
            self.XML_COURSEID1
        )
        self.assertEqual(len(parents), 1)
        self.assertEqual(parents[0], self.course_locations[self.XML_COURSEID1])

class TestMixedUseLocator(TestMixedModuleStore):
    """
    Tests a mixed ms which uses Locators instead of Locations
    """
    REFERENCE_TYPE = 'CourseLocator'

    def setUp(self):
        self.OPTIONS['reference_type'] = 'CourseLocator'
        super(TestMixedUseLocator, self).setUp()

class TestMixedMSInit(unittest.TestCase):
    """
    Test initializing w/o a reference_type
    """
    def setUp(self):
        unittest.TestCase.setUp(self)
        options = copy.copy(OPTIONS)
        del options['reference_type']
        self.connection = pymongo.MongoClient(
            host=HOST,
            port=PORT,
            tz_aware=True,
        )
        self.store = MixedModuleStore(**options)

    def test_use_locations(self):
        """
        Test that use_locations defaulted correctly
        """
        self.assertTrue(self.store.use_locations)
