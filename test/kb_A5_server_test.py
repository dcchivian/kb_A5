from __future__ import print_function
import unittest
import os
import time

from os import environ
from ConfigParser import ConfigParser
import psutil

import requests
from biokbase.workspace.client import Workspace as workspaceService
from biokbase.workspace.client import ServerError as WorkspaceError
from biokbase.AbstractHandle.Client import AbstractHandle as HandleService
from kb_A5.kb_A5Impl import kb_A5
from ReadsUtils.ReadsUtilsClient import ReadsUtils
from kb_A5.kb_A5Server import MethodContext
from pprint import pprint
import inspect

# code taken from kb_SPAdes

class kb_A5Test(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        cls.token = environ.get('KB_AUTH_TOKEN')
        cls.callbackURL = environ.get('SDK_CALLBACK_URL')
        print('CB URL: ' + cls.callbackURL)
        # WARNING: don't call any logging methods on the context object,
        # it'll result in a NoneType error
        cls.ctx = MethodContext(None)
        cls.ctx.update({'token': cls.token,
                        'provenance': [
                            {'service': 'kb_A5',
                             'method': 'please_never_use_it_in_production',
                             'method_params': []
                             }],
                        'authenticated': 1})
        config_file = environ.get('KB_DEPLOYMENT_CONFIG', None)
        cls.cfg = {}
        config = ConfigParser()
        config.read(config_file)
        for nameval in config.items('kb_A5'):
            cls.cfg[nameval[0]] = nameval[1]
        cls.wsURL = cls.cfg['workspace-url']
        cls.shockURL = cls.cfg['shock-url']
        cls.hs = HandleService(url=cls.cfg['handle-service-url'],
                               token=cls.token)
        cls.wsClient = workspaceService(cls.wsURL, token=cls.token)
        wssuffix = int(time.time() * 1000)
        wsName = "test_kb_A5" + str(wssuffix)
        cls.wsinfo = cls.wsClient.create_workspace({'workspace': wsName})
        print('created workspace ' + cls.getWsName())
        cls.serviceImpl = kb_A5(cls.cfg)
        cls.readUtilsImpl = ReadsUtils(cls.callbackURL, token=cls.token)
        cls.staged = {}
        cls.nodes_to_delete = []
        cls.handles_to_delete = []
        cls.setupTestData()
        print('\n\n=============== Starting tests ==================')


    @classmethod
    def tearDownClass(cls):

        print('\n\n=============== Cleaning up ??? ==================')

        if hasattr(cls, 'wsinfo'):
            cls.wsClient.delete_workspace({'workspace': cls.getWsName()})
            print('Test workspace was deleted: ' + cls.getWsName())
        if hasattr(cls, 'nodes_to_delete'):
            for node in cls.nodes_to_delete:
                cls.delete_shock_node(node)
        if hasattr(cls, 'handles_to_delete'):
            cls.hs.delete_handles(cls.hs.ids_to_handles(cls.handles_to_delete))
            print('Deleted handles ' + str(cls.handles_to_delete))


    @classmethod
    def getWsName(cls):
        return cls.wsinfo[1]


    def getImpl(self):
        return self.serviceImpl


    @classmethod
    def delete_shock_node(cls, node_id):
        header = {'Authorization': 'Oauth {0}'.format(cls.token)}
        requests.delete(cls.shockURL + '/node/' + node_id, headers=header,
                        allow_redirects=True)
        print('Deleted shock node ' + node_id)


    # Helper script borrowed from the transform service, logger removed
    @classmethod
    def upload_file_to_shock(cls, file_path):
        """
        Use HTTP multi-part POST to save a file to a SHOCK instance.
        """

        header = dict()
        header["Authorization"] = "Oauth {0}".format(cls.token)

        if file_path is None:
            raise Exception("No file given for upload to SHOCK!")

        with open(os.path.abspath(file_path), 'rb') as dataFile:
            files = {'upload': dataFile}
            print('POSTing data')
            response = requests.post(
                cls.shockURL + '/node', headers=header, files=files,
                stream=True, allow_redirects=True)
            print('got response')

        if not response.ok:
            response.raise_for_status()

        result = response.json()

        if result['error']:
            raise Exception(result['error'][0])
        else:
            return result["data"]


    @classmethod
    def upload_file_to_shock_and_get_handle(cls, test_file):
        '''
        Uploads the file in test_file to shock and returns the node and a
        handle to the node.
        '''
        print('loading file to shock: ' + test_file)
        node = cls.upload_file_to_shock(test_file)
        pprint(node)
        cls.nodes_to_delete.append(node['id'])

        print('creating handle for shock id ' + node['id'])
        handle_id = cls.hs.persist_handle({'id': node['id'],
                                           'type': 'shock',
                                           'url': cls.shockURL
                                           })
        cls.handles_to_delete.append(handle_id)

        md5 = node['file']['checksum']['md5']
        return node['id'], handle_id, md5, node['file']['size']


    @classmethod
    def upload_reads(cls, wsobjname, object_body, fwd_reads,
                     rev_reads=None, single_end=False, sequencing_tech='Illumina',
                     single_genome='1'):

        ob = dict(object_body)  # copy
        ob['sequencing_tech'] = sequencing_tech
        #ob['single_genome'] = single_genome
        ob['wsname'] = cls.getWsName()
        ob['name'] = wsobjname
        if single_end or rev_reads:
            ob['interleaved']= 0
        else:
            ob['interleaved']= 1
        print('\n===============staging data for object ' + wsobjname +
              '================')
        print('uploading forward reads file ' + fwd_reads['file'])
        fwd_id, fwd_handle_id, fwd_md5, fwd_size = \
            cls.upload_file_to_shock_and_get_handle(fwd_reads['file'])

        ob['fwd_id']= fwd_id
        rev_id = None
        rev_handle_id = None
        if rev_reads:
            print('uploading reverse reads file ' + rev_reads['file'])
            rev_id, rev_handle_id, rev_md5, rev_size = \
                cls.upload_file_to_shock_and_get_handle(rev_reads['file'])
            ob['rev_id']= rev_id
        obj_ref = cls.readUtilsImpl.upload_reads(ob)
        objdata = cls.wsClient.get_object_info_new({
            'objects': [{'ref': obj_ref['obj_ref']}]
            })[0]
        cls.staged[wsobjname] = {'info': objdata,
                                 'ref': cls.make_ref(objdata),
                                 'fwd_node_id': fwd_id,
                                 'rev_node_id': rev_id,
                                 'fwd_handle_id': fwd_handle_id,
                                 'rev_handle_id': rev_handle_id
                                 }


    @classmethod
    def upload_empty_data(cls, wsobjname):
        objdata = cls.wsClient.save_objects({
            'workspace': cls.getWsName(),
            'objects': [{'type': 'Empty.AType',
                         'data': {},
                         'name': 'empty'
                         }]
            })[0]
        cls.staged[wsobjname] = {'info': objdata,
                                 'ref': cls.make_ref(objdata),
                                 }


    @classmethod
    def setupTestData(cls):
        print('Shock url ' + cls.shockURL)
        print('WS url ' + cls.wsClient.url)
        print('Handle service url ' + cls.hs.url)
        print('CPUs detected ' + str(psutil.cpu_count()))
        print('Available memory ' + str(psutil.virtual_memory().available))
        print('staging data')
        # get file type from type

        phiX_fwd_reads = {'file': 'data/phiX_p1.fastq',
                     'name': 'phiX_fwd.fastq',
                     'type': 'fastq'}
        # get file type from handle file name
        phiX_rev_reads = {'file': 'data/phiX_p2.fastq',
                     'name': 'phiX_rev.fastq',
                     'type': ''}

        cls.upload_reads('phiX_reads', {}, phiX_fwd_reads, rev_reads=phiX_rev_reads)
        cls.delete_shock_node(cls.nodes_to_delete.pop())
        cls.upload_empty_data('empty')
        print('Data staged.')


    @classmethod
    def make_ref(self, object_info):
        return str(object_info[6]) + '/' + str(object_info[0]) + \
            '/' + str(object_info[4])


    def test_run_A5(self):

        self.run_success(

            [ {'libfile_library':'phiX_reads',
               'libfile_unpaired': '  None  ',
               'libfile_insert': 300 }
            ],
            'phiX_A5_output',
            {'contigs':
             [{'name': 'scaffold_0',
               'length': 5469,
               'id': 'scaffold_0',
               'md5': 'c56ff48c6205e08b20668ec9d9bef437'
               }],
             'md5': 'a4e586937454c45341082c876be12f18',
             'remote_md5': '92205aac3c6fbbda5ae0c12f44a999b7'
             },
            200 )


    def test_no_workspace_param(self):

        self.run_error(
            [{'libfile_library':'foo'}], 'workspace_name parameter is required', wsname=None)


    def test_no_workspace_name(self):

        self.run_error(
            [{'libfile_library': 'foo'}], 'workspace_name parameter is required', wsname='None')


    def test_bad_workspace_name(self):

        self.run_error([{'libfile_library':'foo'}], 'Invalid workspace name bad|name',
                       wsname='bad|name')


    def test_non_extant_workspace(self):

        self.run_error(
            [{'libfile_library': 'foo'}], 'Object foo cannot be accessed: No workspace with name ' +
            'Ireallyhopethisworkspacedoesntexistorthistestwillfail exists',
            wsname='Ireallyhopethisworkspacedoesntexistorthistestwillfail',
            exception=WorkspaceError)


    def test_no_libs_param(self):

        self.run_error(None, 'libfile_args parameter is required')


    def test_non_extant_lib(self):

        self.run_error(
            [{'libfile_library': 'foo'}],
            ('No object with name foo exists in workspace {} ' +
             '(name {})').format(str(self.wsinfo[0]), self.wsinfo[1]),
            exception=WorkspaceError)


    def test_no_libs(self):

        self.run_error([], 'At least one reads library must be provided')


    def test_no_output_param(self):

        self.run_error(
            [{'libfile_library': 'foo'}], 'output_contigset_name parameter is required',
            output_name=None)


    def test_invalid_min_contig(self):

        self.run_error(
            [{'libfile_library': 'foo'}], 'min_contig must be of type int', wsname='fake', output_name='test-output',
            min_contig_len='not an int!')


    def run_error(self, libfile_args, error, wsname=('fake'), output_name='out',
                  min_contig_len=0, exception=ValueError):

        test_name = inspect.stack()[1][3]
        print('\n***** starting expected fail test: ' + test_name + ' *****')
        print('    libs: ' + str(libfile_args))

        if wsname == ('fake'):
            wsname = self.getWsName()

        params = {}
        if (wsname is not None):
            if wsname == 'None':
                params['workspace_name'] = None
            else:
                params['workspace_name'] = wsname

        if (libfile_args is not None):
            params['libfile_args'] = libfile_args

        if (output_name is not None):
            params['output_contigset_name'] = output_name

        params['min_contig_length'] = min_contig_len

        with self.assertRaises(exception) as context:
            self.getImpl().run_A5(self.ctx, params)
        self.assertEqual(error, str(context.exception.message))


    def run_success(self, libfile_args, output_name, expected,
                        min_contig=None, contig_count=None):

        test_name = inspect.stack()[1][3]
        print('\n**** starting expected success test: ' + test_name + ' *****')
        print('   libs: ' + str(libfile_args))

        if not contig_count:
            contig_count = len(expected['contigs'])

        print("READNAMES: " + str(libfile_args))
        print("STAGED: " + str(self.staged))
        pprint(self.staged)

        #libs = [self.staged[n]['info'][1] for n in libfile_args]
        libs = libfile_args

        params = {'workspace_name': self.getWsName(),
                  'libfile_args': libs,
                  'output_contigset_name': output_name,
                  'min_contig' : min_contig
                  }

        print("PARAMS BEFORE CALLING ================== A5")
        pprint(params)
        print("=============  END OF PARAMS TO  ================  A5")

        ret = self.getImpl().run_A5(self.ctx, params)[0]

        print('RESULT from A5:')
        pprint(ret)
        print("====================   END OF RESULT: ")

        report = self.wsClient.get_objects([{'ref': ret['report_ref']}])[0]

        self.assertEqual('KBaseReport.Report', report['info'][2].split('-')[0])
        self.assertEqual(1, len(report['data']['objects_created']))
        self.assertEqual('Assembled contigs',
                         report['data']['objects_created'][0]['description'])
        #self.assertIn('Assembled into ' + str(contig_count) +
        #              ' contigs', report['data']['text_message'])

        print("PROVENANCE: ")
        pprint(report['provenance'])
        print("====================   END OF PROVENANCE: ")

        assembly_ref = report['data']['objects_created'][0]['ref']
        assembly = self.wsClient.get_objects([{'ref': assembly_ref}])[0]
        print("ASSEMBLY OBJECT:")
        pprint(assembly)
        print("===============  END OF ASSEMBLY OBJECT:")

        self.assertEqual('KBaseGenomeAnnotations.Assembly', assembly['info'][2].split('-')[0])

        self.assertEqual(1, len(assembly['provenance']))
        self.assertEqual(output_name, assembly['info'][1])

        temp_handle_info = self.hs.hids_to_handles([assembly['data']['fasta_handle_ref']])
        print("HANDLE OBJECT:")
        pprint(temp_handle_info)
        assembly_fasta_node = temp_handle_info[0]['id']
        self.nodes_to_delete.append(assembly_fasta_node)
        header = {"Authorization": "Oauth {0}".format(self.token)}

        self.assertEqual(contig_count, len(assembly['data']['contigs']))
        self.assertEqual(output_name, assembly['data']['assembly_id'])

        self.assertEqual(expected['md5'], assembly['data']['md5'])

        for exp_contig in expected['contigs']:
            if exp_contig['id'] in assembly['data']['contigs']:
                obj_contig = assembly['data']['contigs'][exp_contig['id']]
                self.assertEqual(exp_contig['name'], obj_contig['name'])
                self.assertEqual(exp_contig['length'], obj_contig['length'])
                self.assertEqual(exp_contig['md5'], obj_contig['md5'])
            else:
                # Hacky way to do this, but need to see all the contig_ids
                # They changed because the IDBA version changed and
                # Need to see them to update the tests accordingly.
                # If code gets here this test is designed to always fail, but show results.
                self.assertEqual(str(assembly['data']['contigs']), "BLAH")







