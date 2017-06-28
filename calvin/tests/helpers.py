from calvin.utilities.nodecontrol import dispatch_node
from calvin.utilities.attribute_resolver import format_index_string
from calvin.utilities import calvinconfig
from calvin.utilities import calvinlogger
from calvin.requests.request_handler import RT
import os
import time
import multiprocessing
import copy
import numbers

_log = calvinlogger.get_logger(__name__)
_conf = calvinconfig.get()

def retry(retries, function, criterion, error_msg):
    """
        Executes 'result = function()' until 'criterion(result)' evaluates to a true value.
        Raises 'Exception(error_msg)' if criterion is not fulfilled after 'retries' attempts
    
    """
    delay = 0.1
    retry = 0
    result = None
    while retry < retries:
        try:
            result = function()
            try:
                if criterion(result):
                    if retry > 0:
                        _log.info("Criterion %s(%s) satisfied after %d retries" %
                                    (str(criterion), str(function), retry,))
                    return result
            except Exception as e:
                _log.error("Erroneous criteria '%r" % (e, ))
                raise e
        except Exception as e:
            _log.exception("Encountered exception when retrying '%s'" % (e,))
            #_log.info("Encountered exception when retrying '%s'" % (e,))
        delay = min(2, delay * 1.5); retry += 1
        time.sleep(delay)
        try:
            r = result if isinstance(result, (numbers.Number, type(None))) else len(result)
        except:
            r = None
        _log.info("Criterion still not satisfied after %d retries, result (length) %s" % (retry, r))
    _log.info("Criterion %s(%s) never satisfied, last full result %s" % (str(criterion), str(function), str(result)))
    raise Exception(error_msg)

def wait_for_tokens(request_handler, rt, actor_id, size=5, retries=10):
    """An alias for 'actual_tokens'"""
    return actual_tokens(request_handler, rt, actor_id, size, retries)

def actual_tokens(request_handler, rt, actor_id, size=5, retries=10):
    """
    Uses 'request_handler' to fetch the report from actor 'actor_id' on runtime 'rt'.
    """
    from functools import partial
    func = partial(request_handler.report, rt, actor_id)
    criterion = lambda tokens: len(tokens) >= size
    return retry(retries, func, criterion, "Not enough tokens, expected %d" % (size,))


def multi_report(request_handler, rt, actor_ids):
    """
    Helper uses 'request_handler' to fetch the report from actors in 'actor_ids' list on runtime(s) 'rt'.
    """
    result = []
    if isinstance(rt, (list, tuple, set)):
        args = zip(rt, actor_ids)
    else:
        args = zip([rt]*len(actor_ids), actor_ids)
    for runtime, actor_id in args:
        result.append(request_handler.report(runtime, actor_id))
    return result

def actual_tokens_multiple(request_handler, rt, actor_ids, size=5, retries=10):
    """
    Uses 'request_handler' to fetch the report from actors in 'actor_ids' list on runtime(s) 'rt'.
    """
    from functools import partial
    func = partial(multi_report, request_handler, rt, actor_ids)
    criterion = lambda tokens: sum([len(t) for t in tokens]) >= size
    return retry(retries, func, criterion, "Not enough tokens, expected %d" % size)

def destroy_app(deployer, retries=10):
    """
    Tries to destroy the app connected with deployer. 
    """
    return delete_app(deployer.request_handler, deployer.runtime, deployer.app_id, retries=retries)


def deploy_app(request_handler, deployer, runtimes, retries=10):
    """
    Deploys app associated w/ deployer and then tries to verify its
    presence in registry (for all runtimes).
    """
    deployer.deploy()
    
    def check_application():
        for rt in runtimes:
            try:
                if request_handler.get_application(rt, deployer.app_id) is None:
                    return False
            except:
                return False
        _log.info("Application found on all peers, continuing")
        return True

    return retry(retries, check_application, lambda r: r, "Application not found on all peers")

def deploy_signed_application(request_handler, rt, name, content, retries=10):
    """
    Deploys app associated w/ deployer and then tries to verify its
    presence in registry (for all runtimes).
    """
    from functools import partial
    return retry(retries, partial(request_handler.deploy_application, rt, name, content['file'], content=content, check=True), lambda _: True, "Failed to deploy application")

def deploy_signed_application_that_should_fail(request_handler, rt, name, content, retries=10):
    """
    Deploys app associated w/ deployer and then tries to verify its
    presence in registry (for all runtimes).
    """
    delay = 0.1
    retry = 0
    result = None
    while retry < retries:
        try:
            result = request_handler.deploy_application(rt, name, content['file'], content=content, check=True)
        except Exception as e:
            if e.message.startswith("401"):
                return
        delay = min(2, delay * 1.5); retry += 1
        time.sleep(delay)
        _log.info("Deployment failed, but not due to security reasons, %d retries" % (retry))
    raise Exception("Deployment of app correctly_signed, did not fail for security reasons")


def delete_app(request_handler, runtime, app_id, check_actor_ids=None, retries=10):
    """
    Deletes an app and then tries to verify it is actually gone.
    """
    from functools import partial

    def verify_app_gone(request_handler, runtime, app_id):
        try:
            request_handler.get_application(runtime, app_id)
            return False
        except:
            return True

    def verify_actors_gone(request_handler, runtime, actor_ids):
        responses = []
        for actor_id in actor_ids:
            responses.append(request_handler.async_get_actor(runtime, actor_id))
        gone = True
        for r in responses:
            try:
                response = request_handler.async_response(r)
                if response is not None:
                    gone = False
            except:
                pass
        return gone

    try:
        request_handler.delete_application(runtime, app_id)
    except Exception as e:
        msg = str(e.message)
        if msg.startswith("500"):
            _log.error("Delete App got 500")
        elif msg.startswith("404"):
            _log.error("Delete App got 404")
        else:
            _log.error("Delete App got unknown error %s" % str(msg))

    retry(retries, partial(verify_app_gone, request_handler, runtime, app_id), lambda r: r, "Application not deleted")
    if check_actor_ids:
        retry(retries, partial(verify_actors_gone, request_handler, runtime, check_actor_ids), 
              lambda r: r, "Application actors not deleted")


def deploy_script(request_handler, name, script, runtime, retries=10):
    """
    Deploys script and then tries to verify its
    presence in registry on the runtime.
    """

    response = request_handler.deploy_application(runtime, name, script)
    app_id = response['application_id']

    def check_application():
        try:
            if request_handler.get_application(runtime, app_id) is None:
                return False
        except:
            return False
        _log.info("Application found, continuing")
        return True

    retry(retries, check_application, lambda r: r, "Application not found")
    return response

def flatten_zip(lz):
    return [] if not lz else [ lz[0][0], lz[0][1] ] + flatten_zip(lz[1:])
    

# Helper for 'std.CountTimer' actor
def expected_counter(n):
    return [i for i in range(1, n + 1)]

# Helper for 'std.Sum' 
def expected_sum(n):
    def cumsum(l):
        s = 0
        for n in l:
            s = s + n
            yield s
        
    return list(cumsum(range(1, n + 1)))

def expected_tokens(request_handler, rt, actor_id, t_type):
    
    tokens = request_handler.report(rt, actor_id)

    if t_type == 'seq':
        return expected_counter(tokens)

    if t_type == 'sum':
        return expected_sum(tokens)

    return None


def setup_distributed(control_uri, purpose, request_handler):
    from functools import partial
    
    remote_node_count = 3
    test_peers = None
    runtimes = []
    
    runtime = RT(control_uri)
    index = {"node_name": {"organization": "com.ericsson", "purpose": purpose}}
    index_string = format_index_string(index)
    
    get_index = partial(request_handler.get_index, runtime, index_string)
    
    def criteria(peers):
        return peers and peers.get("result", None) and len(peers["result"]) >= remote_node_count
    
    test_peers = retry(10, get_index, criteria, "Not all nodes found")
    test_peers = test_peers["result"]
    
    for peer_id in test_peers:
        peer = request_handler.get_node(runtime, peer_id)
        if not peer:
            _log.warning("Runtime '%r' peer '%r' does not exist" % (runtime, peer_id, ))
            continue
        rt = RT(peer["control_uri"])
        rt.id = peer_id
        rt.uris = peer["uri"]
        runtimes.append(rt)

    return runtimes
    
def setup_local(ip_addr, request_handler, nbr, proxy_storage):  
    def check_storage(rt, n, index):
        index_string = format_index_string(index)
        retries = 0
        while retries < 120:
            try:
                retries += 1
                peers = request_handler.get_index(rt, index_string, timeout=60)
            except Exception as e:
                try:
                    notfound = e.message.startswith("404")
                except:
                    notfound = False
                if notfound:
                    peers={'result':[]}
                else:
                    _log.info("Timed out when finding peers retrying")
                    retries += 39  # A timeout counts more we don't want to wait 60*100 seconds
                    continue
            if len(peers['result']) >= n:
                _log.info("Found %d peers (%r)", len(peers['result']), peers['result'])
                return
            _log.info("Only %d peers found (%r)", len(peers['result']), peers['result'])
            time.sleep(1)
        # No more retrying
        raise Exception("Storage check failed, could not find peers.")

    hosts = [
        ("calvinip://%s:%d" % (ip_addr, d), "http://%s:%d" % (ip_addr, d+1)) for d in range(5200, 5200 + 2 * nbr, 2)
    ]

    runtimes = []

    host = hosts[0]
    attr = {u'indexed_public': {u'node_name': {u'organization': u'com.ericsson', u'purpose': u'distributed-test'}}}
    attr_first = copy.deepcopy(attr)
    attr_first['indexed_public']['node_name']['group'] = u'first'
    attr_first['indexed_public']['node_name']['name'] = u'runtime1'
    attr_rest = copy.deepcopy(attr)
    attr_rest['indexed_public']['node_name']['group'] = u'rest'

    _log.info("starting runtime %s %s" % host)
    
    if proxy_storage:
        import calvin.runtime.north.storage
        calvin.runtime.north.storage._conf.set('global', 'storage_type', 'local')
    rt, _ = dispatch_node([host[0]], host[1], attributes=attr_first)
    check_storage(rt, len(runtimes)+1, attr['indexed_public'])
    runtimes += [rt]
    if proxy_storage:
        import calvin.runtime.north.storage
        calvin.runtime.north.storage._conf.set('global', 'storage_type', 'proxy')
        calvin.runtime.north.storage._conf.set('global', 'storage_proxy', host[0])
    _log.info("started runtime %s %s" % host)

    count = 2
    for host in hosts[1:]:
        if nbr > 3:
            # Improve likelihood of success if runtimes started with a time interval
            time.sleep(10.0)
        _log.info("starting runtime %s %s" % host)
        attr_rt = copy.deepcopy(attr_rest)
        attr_rt['indexed_public']['node_name']['name'] = u'runtime' + str(count)
        count += 1
        rt, _ = dispatch_node([host[0]], host[1], attributes=attr_rt)
        check_storage(rt, len(runtimes)+1, attr['indexed_public'])
        _log.info("started runtime %s %s" % host)
        runtimes += [rt]

    for host in hosts:
        check_storage(RT(host[1]), nbr, attr['indexed_public'])
        
    for host in hosts:
        request_handler.peer_setup(RT(host[1]), [h[0] for h in hosts if h != host])
    
    return runtimes

def setup_bluetooth(bt_master_controluri, request_handler):
    runtime = RT(bt_master_controluri)
    runtimes = []
    bt_master_id = request_handler.get_node_id(bt_master_controluri)
    data = request_handler.get_node(runtime, bt_master_id)
    if data:
        runtime.id = bt_master_id
        runtime.uris = data["uri"]
        test_peers = request_handler.get_nodes(runtime)
        test_peer2_id = test_peers[0]
        test_peer2 = request_handler.get_node(runtime, test_peer2_id)
        if test_peer2:
            rt2 = RT(test_peer2["control_uri"])
            rt2.id = test_peer2_id
            rt2.uris = test_peer2["uri"]
            runtimes.append(rt2)
        test_peer3_id = test_peers[1]
        if test_peer3_id:
            test_peer3 = request_handler.get_node(runtime, test_peer3_id)
            if test_peer3:
                rt3 = request_handler.RT(test_peer3["control_uri"])
                rt3.id = test_peer3_id
                rt3.uris = test_peer3["uri"]
                runtimes.append(rt3)
    return [runtime] + runtimes

def setup_test_type(request_handler, nbr=3, proxy_storage=False):
    control_uri = None
    ip_addr = None
    purpose = None
    bt_master_controluri = None
    test_type = None

    try:
        control_uri = os.environ["CALVIN_TEST_CTRL_URI"]
        purpose = os.environ["CALVIN_TEST_UUID"]
        test_type = "distributed"
    except KeyError:
        pass

    if not test_type:
        # Bluetooth tests assumes one master runtime with two connected peers
        # CALVIN_TEST_BT_MASTERCONTROLURI is the control uri of the master runtime
        try:
            bt_master_controluri = os.environ["CALVIN_TEST_BT_MASTERCONTROLURI"]
            _log.debug("Running Bluetooth tests")
            test_type = "bluetooth"
        except KeyError:
            pass

    if not test_type:
        try:
            ip_addr = os.environ["CALVIN_TEST_LOCALHOST"]
        except KeyError:
            import socket
            # If this fails add hostname to the /etc/hosts file for 127.0.0.1
            ip_addr = socket.gethostbyname(socket.gethostname())
        test_type = "local"

    if test_type == "distributed":
        runtimes = setup_distributed(control_uri, purpose, request_handler)
    elif test_type == "bluetooth":
        runtimes = setup_bluetooth(bt_master_controluri, request_handler)
    else:
        proxy_storage = bool(int(os.environ.get("CALVIN_TESTING_PROXY_STORAGE", proxy_storage)))
        runtimes = setup_local(ip_addr, request_handler, nbr, proxy_storage)

    return test_type, runtimes
    

def teardown_test_type(test_type, runtimes, request_handler):
    from functools import partial
    def wait_for_it(peer):
        while True:
            try:
                request_handler.get_node_id(peer)
            except Exception:
                return True
        return False
        
    if test_type == "local":
        for peer in runtimes:
            request_handler.quit(peer)
            retry(10, partial(request_handler.get_node_id, peer), lambda _: True, "Failed to stop peer %r" % (peer,))
            # wait_for_it(peer)
        for p in multiprocessing.active_children():
            p.terminate()
            time.sleep(1)

def sign_files_for_security_tests(credentials_testdir):
    from calvin.utilities import code_signer
    from calvin.utilities.utils import get_home
    from calvin.utilities import certificate
    import shutil
    def replace_text_in_file(file_path, text_to_be_replaced, text_to_insert):
        # Read in the file
        filedata = None
        with open(file_path, 'r') as file :
              filedata = file.read()

        # Replace the target string
        filedata = filedata.replace(text_to_be_replaced, text_to_insert)

        # Write the file out again
        with open(file_path, 'w') as file:
            file.write(filedata)

    homefolder = get_home()
    runtimesdir = os.path.join(credentials_testdir,"runtimes")
    runtimes_truststore_signing_path = os.path.join(runtimesdir,"truststore_for_signing")
    orig_testdir = os.path.join(os.path.dirname(__file__), "security_test")
    orig_actor_store_path = os.path.abspath(os.path.join(os.path.dirname( __file__ ), '..', 'actorstore','systemactors'))
    actor_store_path = os.path.join(credentials_testdir, "store")
    orig_application_store_path = os.path.join(orig_testdir, "scripts")
    application_store_path = os.path.join(credentials_testdir, "scripts")
    print "Create test folders"
    try:
        os.makedirs(actor_store_path)
        os.makedirs(os.path.join(actor_store_path,"test"))
        shutil.copy(os.path.join(orig_actor_store_path,"test","__init__.py"), os.path.join(actor_store_path,"test","__init__.py"))
        os.makedirs(os.path.join(actor_store_path,"std"))
        shutil.copy(os.path.join(orig_actor_store_path,"std","__init__.py"), os.path.join(actor_store_path,"std","__init__.py"))
        shutil.copytree(orig_application_store_path, application_store_path)
    except Exception as err:
        _log.error("Failed to create test folder structure, err={}".format(err))
        print "Failed to create test folder structure, err={}".format(err)
        raise

    print "Trying to create a new test application/actor signer."
    cs = code_signer.CS(organization="testsigner", commonName="signer", security_dir=credentials_testdir)

    #Create signed version of CountTimer actor
    orig_actor_CountTimer_path = os.path.join(orig_actor_store_path,"std","CountTimer.py")
    actor_CountTimer_path = os.path.join(actor_store_path,"std","CountTimer.py")
    shutil.copy(orig_actor_CountTimer_path, actor_CountTimer_path)
    cs.sign_file(actor_CountTimer_path)

    #Create unsigned version of CountTimer actor
    actor_CountTimerUnsigned_path = actor_CountTimer_path.replace(".py", "Unsigned.py") 
    shutil.copy(actor_CountTimer_path, actor_CountTimerUnsigned_path)
    replace_text_in_file(actor_CountTimerUnsigned_path, "CountTimer", "CountTimerUnsigned")

    #Create signed version of Sum actor
    orig_actor_Sum_path = os.path.join(orig_actor_store_path,"std","Sum.py")
    actor_Sum_path = os.path.join(actor_store_path,"std","Sum.py")
    shutil.copy(orig_actor_Sum_path, actor_Sum_path)
    cs.sign_file(actor_Sum_path)

    #Create unsigned version of Sum actor
    actor_SumUnsigned_path = actor_Sum_path.replace(".py", "Unsigned.py") 
    shutil.copy(actor_Sum_path, actor_SumUnsigned_path)
    replace_text_in_file(actor_SumUnsigned_path, "Sum", "SumUnsigned")

    #Create incorrectly signed version of Sum actor
    actor_SumFake_path = actor_Sum_path.replace(".py", "Fake.py") 
    shutil.copy(actor_Sum_path, actor_SumFake_path)
    #Change the class name to SumFake
    replace_text_in_file(actor_SumFake_path, "Sum", "SumFake")
    cs.sign_file(actor_SumFake_path)
    #Now append to the signed file so the signature verification fails
    with open(actor_SumFake_path, "a") as fd:
            fd.write(" ")

    #Create signed version of Sink actor
    orig_actor_Sink_path = os.path.join(orig_actor_store_path,"test","Sink.py")
    actor_Sink_path = os.path.join(actor_store_path,"test","Sink.py")
    shutil.copy(orig_actor_Sink_path, actor_Sink_path)
    cs.sign_file(actor_Sink_path)

    #Create unsigned version of Sink actor
    actor_SinkUnsigned_path = actor_Sink_path.replace(".py", "Unsigned.py") 
    shutil.copy(actor_Sink_path, actor_SinkUnsigned_path)
    replace_text_in_file(actor_SinkUnsigned_path, "Sink", "SinkUnsigned")

    #Sign applications
    cs.sign_file(os.path.join(application_store_path, "correctly_signed.calvin"))
    cs.sign_file(os.path.join(application_store_path, "correctlySignedApp_incorrectlySignedActor.calvin"))
    cs.sign_file(os.path.join(application_store_path, "incorrectly_signed.calvin"))
    #Now append to the signed file so the signature verification fails
    with open(os.path.join(application_store_path, "incorrectly_signed.calvin"), "a") as fd:
            fd.write(" ")

    print "Export Code Signers certificate to the truststore for code signing"
    out_file = cs.export_cs_cert(runtimes_truststore_signing_path)
    certificate.c_rehash(type=certificate.TRUSTSTORE_SIGN, security_dir=credentials_testdir)
    return actor_store_path, application_store_path

def fetch_and_log_runtime_actors(rt, request_handler):
    from functools import partial
    # Verify that actors exist like this
    actors=[]
    #Use admins credentials to access the control interface
    request_handler.set_credentials({"user": "user0", "password": "pass0"})
    for runtime in rt:
        actors_rt = retry(20, partial(request_handler.get_actors, runtime), lambda _: True, "Failed to get actors")
        actors.append(actors_rt)
    for i in range(len(rt)):
        _log.info("\n\trt{} actors={}".format(i, actors[i]))
    return actors

def security_verify_storage(rt, request_handler):
    from functools import partial
    _log.info("Let's verify storage, rt={}".format(rt))
    rt_id=[None]*len(rt)
    # Wait for control API to be up and running
    for j in range(len(rt)):
        rt_id[j] = retry(30, partial(request_handler.get_node_id, rt[j]), lambda _: True, "Failed to get node id")
    _log.info("RUNTIMES:{}".format(rt_id))
    # Wait for storage to be connected
    index = "node/capabilities/calvinsys.native.python-json" 
    failed = True
    def test():
        count=[0]*len(rt)
        caps =[0]*len(rt)
        #Loop through all runtimes to ask them which runtimes nodes they know with calvisys.native.python-json
        for j in range(len(rt)):
            caps[j] = retry(30, partial(request_handler.get_index, rt[j], index), lambda _: True, "Failed to get index")['result']
            #Add the known nodes to statistics of how many nodes store keys from that node
            for k in range(len(rt)):
                count[k] = count[k] + caps[j].count(rt_id[k])
        _log.info("rt_ids={}\n\tcount={}".format(rt_id, count))
        return count

    criterion = lambda count: (x >=min(5, len(rt)) for x in count)
    retry(30, test, criterion, "Storage has not spread as it should")
    #Loop through all runtimes and make sure they can lookup all other runtimes
    for runtime1 in rt:
        for runtime2 in rt:
            node_name = runtime2.attributes['indexed_public']['node_name']
            index = format_index_string(['node_name', node_name])
            retry(10, partial(request_handler.get_index, runtime1, index), lambda _: True, "Failed to get index")
    return True

def create_CA(domain_name, credentials_testdir, NBR_OF_RUNTIMES):
    runtimes, ca = _create_CA_and_rehash(domain_name, credentials_testdir, NBR_OF_RUNTIMES)
    return runtimes


def create_CA_and_get_enrollment_passwords(domain_name, credentials_testdir, NBR_OF_RUNTIMES):
    runtimes, ca = _create_CA_and_rehash(domain_name, credentials_testdir, NBR_OF_RUNTIMES)
    get_enrollment_passwords(runtimes, method="ca", ca=ca)
    return runtimes

def create_CA_and_generate_runtime_certs(domain_name, credentials_testdir, NBR_OF_RUNTIMES):
    runtimes, ca = _create_CA_and_rehash(domain_name, credentials_testdir, NBR_OF_RUNTIMES)
    get_enrollment_passwords(runtimes, method="ca", ca=ca)
    _generate_certiticates(ca, runtimes, domain_name, credentials_testdir)
    return runtimes

def _create_CA_and_rehash(domain_name, credentials_testdir, NBR_OF_RUNTIMES):
    from calvin.utilities import certificate
    from calvin.utilities import certificate_authority
    runtimesdir = os.path.join(credentials_testdir,"runtimes")
    runtimes_truststore = os.path.join(runtimesdir,"truststore_for_transport")
    print "Trying to create a new test domain configuration."
    ca = certificate_authority.CA(domain=domain_name, commonName="testdomain CA", security_dir=credentials_testdir)

    print "Copy CA cert into truststore of runtimes folder"
    ca.export_ca_cert(runtimes_truststore)
    certificate.c_rehash(type=certificate.TRUSTSTORE_TRANSPORT, security_dir=credentials_testdir)
    runtimes=[]
    for i in range(NBR_OF_RUNTIMES):
        runtimes.append({})
    #Define the runtime attributes
    _runtime_attributes(domain_name, runtimes)
    _get_node_names(runtimes)
    for i in range(NBR_OF_RUNTIMES):
        node_name = runtimes[i]["node_name"]
        if "testNode0" in node_name:
            ca.add_new_authentication_server(node_name)
            ca.add_new_authorization_server(node_name)
    return runtimes, ca

def _get_node_names(runtimes):
    from calvin.utilities.attribute_resolver import AttributeResolver
    from calvin.utilities import calvinuuid
    from copy import deepcopy
    for i in range(len(runtimes)):
        rt_attribute = deepcopy(runtimes[i]["attributes"])
        attributes=AttributeResolver(rt_attribute)
        runtimes[i]["node_name"] = attributes.get_node_name_as_str()
        runtimes[i]["id"]= calvinuuid.uuid("")


def get_enrollment_passwords(runtimes, method="ca", rt=None, request_handler=None, ca=None):
    from functools import partial
    #Set/get enrollment for the other runtimes:
    for i in range(len(runtimes)):
        node_name = runtimes[i]["node_name"]
        if method=="ca" and ca:
            runtimes[i]["enrollment_password"] = ca.cert_enrollment_add_new_runtime(node_name)
        elif method=="controlapi_get" and rt:
            enrollment_password = retry(10,
                                        partial(request_handler.get_enrollment_password, rt[0], node_name),
                                        lambda _: True, "Failed to get enrollment password")
            runtimes[i]["enrollment_password"] = enrollment_password
        elif method=="controlapi_set":
            enrollment_password = "abrakadabra123456789"
            runtimes[i]["enrollment_password"] = enrollment_password
            retry(  10,
                    partial(request_handler.set_enrollment_password, rt[0], node_name, enrollment_password),
                    lambda _: True, "Failed to get enrollment password")
            runtimes[i]["enrollment_password"] = enrollment_password
        else:
            raise Exception("get_enrollment_passwords: incorrect arguments")

def _generate_certiticates(ca, runtimes, domain_name, credentials_testdir):
    from calvin.utilities import runtime_credentials
    for i in range(len(runtimes)):
        node_name = runtimes[i]["node_name"]
        runtime=runtime_credentials.RuntimeCredentials(node_name,
                                                       domain=domain_name,
#                                                           hostnames=["elxahyc5lz1","elxahyc5lz1.localdomain"],
                                                       security_dir=credentials_testdir,
                                                       nodeid=runtimes[i]["id"],
                                                       enrollment_password=runtimes[i]["enrollment_password"])
        runtimes[i]["credentials"]= runtime
        csr_path = os.path.join(runtime.runtime_dir, node_name + ".csr")
        #Decrypt encrypted CSR with CAs private key
        rsa_encrypted_csr = runtime.get_encrypted_csr()
        csr = ca.decrypt_encrypted_csr(encrypted_enrollment_request=rsa_encrypted_csr)
        csr_path = ca.store_csr_with_enrollment_password(csr)
        cert_path = ca.sign_csr(csr_path)
        runtime.store_own_cert(certpath=cert_path, security_dir=credentials_testdir)


def _runtime_attributes(domain_name, runtimes):
    #Define the runtime attributes
    org_name='org.testexample'
    domain_name="test_security_domain"
    for i in range(len(runtimes)):
        print i," "
        purpose = 'CA-authserver-authzserver' if i==0 else ""
        node_name ={'organization': org_name,
                     'purpose':purpose,
                     'name': 'testNode{}'.format(i)}
        print node_name
        owner = {'organization': domain_name, 'personOrGroup': 'testOwner'}
        address = {'country': 'SE', 'locality': 'testCity', 'street': 'testStreet', 'streetNumber': 1}
        runtimes[i]["attributes"] = {
                            'indexed_public':
                            {
                                'owner':owner,
                                'node_name':node_name,
                                'address':address
                            }
                        }

def wait_for_runtime(request_handler, rt):
    from functools import partial
    rt_id = retry(100, partial(request_handler.get_node_id, rt), lambda _: True, "Failed to get node id")

def start_runtime0(runtimes, rt, hostname, request_handler, tls=False, enroll_passwords=False):
    from calvin.Tools.csruntime import csruntime
    from conftest import _config_pytest
    #Start runtime 0 as it takes alot of time to start, and needs to be up before the others start
    _log.info("Starting runtime 0")
    try:
        logfile = _config_pytest.getoption("logfile")+"5000"
        outfile = os.path.join(os.path.dirname(logfile), os.path.basename(logfile).replace("log", "out"))
        if outfile == logfile:
            outfile = None
    except:
        logfile = None
        outfile = None
    csruntime(hostname, port=5000, controlport=5020, attr=runtimes[0]["attributes"],
               loglevel=_config_pytest.getoption("loglevel"), logfile=logfile, outfile=outfile,
               configfile="/tmp/calvin5000.conf")
    uri = "https://{}:5020".format(hostname) if tls==True else "http://{}:5020".format(hostname)
    rt.append(RT(uri))
    rt[0].attributes=runtimes[0]["attributes"]

    #Wait for runtime 0 to be up and running
    wait_for_runtime(request_handler, rt[0])

def start_other_runtimes(runtimes, rt, hostname, request_handler, tls=False):
    from calvin.Tools.csruntime import csruntime
    from conftest import _config_pytest
    #Start the other runtimes
    for i in range(1, len(runtimes)):
        _log.info("Starting runtime {}".format(i))
        try:
            logfile = _config_pytest.getoption("logfile")+"500{}".format(i)
            outfile = os.path.join(os.path.dirname(logfile), os.path.basename(logfile).replace("log", "out"))
            if outfile == logfile:
                outfile = None
        except:
            logfile = None
            outfile = None
        csruntime(hostname,
                  port=5000+i,
                  controlport=5020+i,
                  attr=runtimes[i]["attributes"],
                  loglevel=_config_pytest.getoption("loglevel"),
                  logfile=logfile,
                  outfile=outfile,
                  configfile="/tmp/calvin500{}.conf".format(i))
        uri = "https://{}:502{}".format(hostname, i) if tls==True else "http://{}:502{}".format(hostname, i)
        rt.append(RT(uri))
        rt[i].attributes=runtimes[i]["attributes"]
        time.sleep(0.1)

def start_all_runtimes(runtimes, hostname, request_handler, tls=False):
    rt=[]
    start_runtime0(runtimes, rt, hostname, request_handler, tls=tls)
    start_other_runtimes(runtimes, rt, hostname, request_handler, tls=tls)
    return rt

def teardown_slow(rt, request_handler, hostname):
    request_handler.set_credentials({"user": "user0", "password": "pass0"})
    for i in range(1, len(rt)):
        _log.info("kill runtime {}".format(i))
        try:
            request_handler.quit(rt[i])
        except Exception:
            _log.error("Failed quit for node {}".format(i))
    # Kill Auth/Authz node last since the other nodes need it for authorization
    # of the kill requests
    time.sleep(2)
    try:
        request_handler.quit(rt[0])
    except Exception:
        _log.error("Failed quit for node 0")
    time.sleep(0.2)
    for p in multiprocessing.active_children():
        p.terminate()
    # They will die eventually (about 5 seconds) in most cases, but this makes sure without wasting time
    for i in range(len(rt)):
        os.system("pkill -9 -f 'csruntime -n {} -p 500{}'" .format(hostname,i))
    time.sleep(0.2)

def teardown(rt, request_handler, hostname):
    request_handler.set_credentials({"user": "user0", "password": "pass0"})
    for runtime in rt:
        request_handler.quit(runtime)
    time.sleep(0.2)
    for p in multiprocessing.active_children():
        p.terminate()
    # They will die eventually (about 5 seconds) in most cases, but this makes sure without wasting time
    for i in range(len(rt)):
        os.system("pkill -9 -f 'csruntime -n {} -p 500{}'" .format(hostname,i))
    time.sleep(0.2)

