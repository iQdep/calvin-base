# -*- coding: utf-8 -*-

# Copyright (c) 2015 Ericsson AB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Methods modified from Kademlia with Copyright (c) 2014 Brian Muller:
# transferKeyValues, get_concat, _nodesFound, _handleFoundValues
# see https://github.com/bmuller/kademlia/blob/master/LICENSE

import json
import uuid
import types
import os
import hashlib
import base64
from collections import Counter

from twisted.internet import defer, task, reactor
from kademlia.network import Server
from kademlia.protocol import KademliaProtocol
from kademlia import crawling
from kademlia.utils import deferredDict, digest
from kademlia.storage import ForgetfulStorage
from kademlia.node import Node, NodeHeap
from kademlia import version as kademlia_version
from calvin.utilities import certificate

from twisted.python import log
from calvin.utilities import calvinlogger
from calvin.utilities import calvinconfig
from calvin.utilities import runtime_credentials

_conf = calvinconfig.get()
_log = calvinlogger.get_logger(__name__)

# Make twisted (rpcudp) logs go to null
log.startLogging(log.NullFile(), setStdout=0)


def logger(node, message, level=None):
    _log.debug("{}:{}:{} - {}".format(node.id.encode("hex").upper(),
                                     node.ip,
                                     node.port,
                                     message))
    #print("{}:{}:{} - {}".format(node.id.encode("hex").upper(),
    #                                 node.ip,
    #                                 node.port,
    #                                 message))

def generate_challenge():
    """ Generate a random challenge of 8 bytes, hex string formated"""
    return os.urandom(8).encode("hex")

def dhtidhex_from_certstring(cert_str):
    nodeid = certificate.cert_DN_Qualifier(certstring=cert_str)
    dhtid = dhtid_from_nodeid(nodeid) 
    dhtidhex=dhtid.encode("hex").upper()
    _log.debug("dhtidhex_from_certstring returns:\n\tnodeid={}\n\tdhtid={}".format(nodeid, dhtidhex))
    return dhtidhex

def nodeid_from_dhtid(dhtid):
    import uuid as sys_uuid
    nodeid = str(sys_uuid.UUID(dhtid))
    _log.debug("nodeid_from_dhtid returns:\n\tnodeid={}\n\tdhtid={}".format(nodeid, dhtid))
    return nodeid

def dhtid_from_nodeid(nodeid):
    import uuid as sys_uuid
    dhtid = sys_uuid.UUID(nodeid).bytes
    _log.debug("dhtid_from_nodeid returns:\n\tnodeid={}\n\tdhtid={}".format(nodeid, dhtid))
    return dhtid

# Fix for None types in storage
class ForgetfulStorageFix(ForgetfulStorage):
    def get(self, key, default=None):
        self.cull()
        if key in self.data:
            return (True, self[key])
        return (False, default)


class KademliaProtocolAppend(KademliaProtocol):

    def __init__(self, *args, **kwargs):
        _log.debug("KademliaProtocolAppend::__init__:\n\targs={}\n\tkwargs={}".format(args,kwargs))
        self.set_keys = kwargs.pop('set_keys', set([]))

        self.priv_key = None
        self.node_name = kwargs.pop('node_name',None)
        self.runtime_credentials = kwargs.pop('runtime_credentials', None)
        KademliaProtocol.__init__(self, *args, **kwargs)

    #####################
    # Call Functions    #
    #####################

    def callCertFindValue(self, nodeToAsk, nodeToFind):
        """
        Asks 'nodeToAsk' for its certificate.
        """
        _log.debug("callCertFindValue:\n\tnodeToAsk={}\n\tnodeToFind={}".format(nodeToAsk, nodeToFind))
        address = (nodeToAsk.ip, nodeToAsk.port)
        challenge = generate_challenge()
        try:
            signature = self.runtime_credentials.sign_data(
                                            nodeToAsk.id.encode("hex").upper() + challenge)
        except:
            logger(self.sourceNode, "RETNONE: Signing of certFindValue failed")
            return None
        d = self.find_value(address,
                           self.sourceNode.id,
                           nodeToFind.id,
                           challenge,
                           signature,
                           self.getOwnCert())
        return d.addCallback(self.handleCertCallResponse,
                            nodeToAsk,
                            challenge)


    def callFindNode(self, nodeToAsk, nodeToFind):
        """
        Asks 'nodeToAsk' for the value 'nodeToFind.id'
        """
        _log.debug("callFindNode\n\tnodeToAsk={}\n\tnodeToFind={}".format(nodeToAsk, nodeToFind))
        address = (nodeToAsk.ip, nodeToAsk.port)
        challenge = generate_challenge()
        try:
            signature = self.runtime_credentials.sign_data(
                                            nodeToAsk.id.encode("hex").upper() + challenge)
        except:
            logger(self.sourceNode, "RETNONE: Signing of findNode failed")
            return None
        d = self.find_node(address,
                          self.sourceNode.id,
                          nodeToFind.id,
                          challenge,
                          signature)
        return d.addCallback(self.handleSignedBucketResponse,
                            nodeToAsk,
                            challenge)

    def callFindValue(self, nodeToAsk, nodeToFind):
        """
        Asks 'nodeToAsk' for the information regarding the node 'nodeToFind'
        """
        logger(self.sourceNode,"callFindValue:\n\tnodeToAsk={}\n\tnodeToFind={}".format(nodeToAsk, nodeToFind))
        address = (nodeToAsk.ip, nodeToAsk.port)
        challenge = generate_challenge()
        try:
            signature = self.runtime_credentials.sign_data(
                                            nodeToAsk.id.encode("hex").upper() + challenge)
        except:
            logger(self.sourceNode, "RETNONE: Signing of findValue failed")
            return None
        d = self.find_value(address,
                           self.sourceNode.id,
                           nodeToFind.id,
                           challenge,
                           signature)
        return d.addCallback(self.handleSignedValueResponse,
                            nodeToAsk,
                            challenge)

    def callPing(self, nodeToAsk, cert=None):
        """
        Sends a ping message to 'nodeToAsk'
        """ 
        logger(self.sourceNode,"callPing, nodeToAsk={}".format(nodeToAsk))
        address = (nodeToAsk.ip, nodeToAsk.port)
        challenge = generate_challenge()
        try:
            signature = self.runtime_credentials.sign_data(
                                            nodeToAsk.id.encode("hex").upper() + challenge)
        except:
            logger(self.sourceNode, "RETNONE: Signing of ping failed")
            return None
        d = self.ping(address,
                     self.sourceNode.id,
                     challenge,
                     signature,
                     cert)
        return d.addCallback(self.handleSignedPingResponse,
                            nodeToAsk,
                            challenge)

    def callStore(self, nodeToAsk, key, value):
        """
        Sends a request for 'nodeToAsk' to store value 'value' with key 'key'
        """   
        logger(self.sourceNode,"callStore:\n\tnodeAsking.id={}\n\tnodeAsking={}\n\tnodeToAsk.id={}\n\tnodeToAsk={}\n\tkey={}\n\tvalue={}".format(self.sourceNode.id, self.sourceNode, nodeToAsk.id, nodeToAsk, key.encode("hex"), value))
        address = (nodeToAsk.ip, nodeToAsk.port)
        challenge = generate_challenge()
        try:
            signature = self.runtime_credentials.sign_data(
                                            nodeToAsk.id.encode("hex").upper() + challenge)
        except:
            logger(self.sourceNode, "RETNONE: Signing of store failed")
            return None
        d = self.store(address,
                      self.sourceNode.id,
                      key,
                      value,
                      challenge,
                      signature)
        logger(self.sourceNode, "callStore initiated")
        return d.addCallback(self.handleSignedStoreResponse,
                            nodeToAsk,
                            challenge)

    def callAppend(self, nodeToAsk, key, value):
        """
        Sends a request for 'nodeToAsk' to add value 'value' to key 'key' set
        """   
        logger(self.sourceNode,"callAppend:\n\tnodeToAsk={}\n\tkey={}\n\tvalue={}".format(nodeToAsk, key, value))
        address = (nodeToAsk.ip, nodeToAsk.port)
        challenge = generate_challenge()
        try:
            signature = self.runtime_credentials.sign_data(
                                            nodeToAsk.id.encode("hex").upper() + challenge)
        except:
            logger(self.sourceNode, "RETNONE: Signing of append failed")
            return None
        d = self.append(address,
                        self.sourceNode.id,
                        key,
                        value,
                        challenge,
                        signature)
        return d.addCallback(self.handleSignedStoreResponse, nodeToAsk, challenge)

    def callRemove(self, nodeToAsk, key, value):
        """
        Sends a request for 'nodeToAsk' to remove value 'value' from key 'key' set
        """   
        logger(self.sourceNode,"callRemove:\n\tnodeToAsk={}\n\tkey={}\n\tvalue={}".format(nodeToAsk, key, value))
        address = (nodeToAsk.ip, nodeToAsk.port)
        challenge = generate_challenge()
        try:
            signature = self.runtime_credentials.sign_data(
                                            nodeToAsk.id.encode("hex").upper() + challenge)
        except:
            logger(self.sourceNode, "RETNONE: Signing of append failed")
            return None
        d = self.remove(address,
                        self.sourceNode.id,
                        key,
                        value,
                        challenge,
                        signature)
        return d.addCallback(self.handleSignedStoreResponse, nodeToAsk, challenge)

    #####################
    # Response handlers #
    #####################

    def handleCertCallResponse(self, result, node, challenge):
        """
        Handle Certificate responses. `result` is a response value array
        Element 0 contains ??, Element 1 is a dictionary that contains a
        certificate.

        `node` is responding node and `challenge` is the returned
        challenge value.
        Raise ?? exceptions at ?? occation.
        Return results if signatures are valid?
        """
        logger(self.sourceNode,"handleCertCallResponse, result={}, node={}, challenge={}".format(result, node, challenge))
        logger(self.sourceNode, "handleCertCallResponse {}".format(str(result)))
        cert_str = result[1]['value']
        signature = result[1]['signature']
        if 'value' in result[1]:
            try:
                id = dhtidhex_from_certstring(cert_str)
            except:
                logger(self.sourceNode, "RETFALSENONE: Invalid certificate "
                                        "response from {}".format(node))
                return (False, None)
            if node.id.encode('hex').upper() == id:
                try:
                    self.runtime_credentials.verify_signed_data_from_certstring(
                                                                        cert_str,
                                                                        signature,
                                                                        challenge,
                                                                        certificate.TRUSTSTORE_TRANSPORT)
                except:
                    logger(self.sourceNode,
                          "Invalid signature on certificate "
                          "response from {}".format(node))
                self.router.addContact(node)
                self.storeCert(cert_str, id)
                if self.router.isNewNode(node):
                    self.transferKeyValues(node)
            else:
                logger(self.sourceNode, "RETFALSENONE: Certificate from {} does not match claimed node id".format(node))
                return (False, None)
        else:
            self.router.removeContact(node)
        return result

    def handleSignedBucketResponse(self, result, node, challenge):
        """
        ???
        `result` is an array and element 1 contains a dict.
        Return None if any error occur and dont tell anyone.
        """
        logger(self.sourceNode,"handleSignedBucketResponse, result={}, node={}, challenge={}".format(result, node, challenge))
        logger(self.sourceNode, "handleSignedBucketResponse {}".format(str(result)))
        nodeIdHex = node.id.encode('hex').upper()
        if result[0]:
            if "NACK" in result[1]:
                return (False, self.handleSignedNACKResponse(result, node, challenge))
            elif 'bucket' in result[1] and 'signature' in result[1]:
                cert_stored = self.searchForCertificate(nodeIdHex)
                if cert_stored == None:
                    logger(self.sourceNode,
                           "RETFALSENONE: Certificate for sender of bucket:"
                           " {} not present in store".format(node))
                    return (False, None)
                try:
                    self.runtime_credentials.verify_signed_data_from_certstring(
                                                                        cert_stored,
                                                                        result[1]['signature'],
                                                                        challenge,
                                                                        certificate.TRUSTSTORE_TRANSPORT)
                    self.router.addContact(node)
                    newbucket = list()
                    for bucketnode in result[1]['bucket']:
                        buId = bucketnode[0]
                        buIdHex = buId.encode('hex').upper()
                        buIp = bucketnode[1]
                        buPort = bucketnode[2]
                        if not self.certificateExists(buIdHex):
                            nonCertifiedNode = Node(buId,
                                                    buIp,
                                                    buPort)
                            buIdDigest = digest(str(buIdHex))
                            buIdReq = "{}cert".format(Node(buIdDigest))
                            self.callCertFindValue(nonCertifiedNode,
                                                  buIdReq)
                        else:
                            newbucket.append(bucketnode)
                    return (result[0], newbucket)
                except:
                    logger(self.sourceNode,
                          "RETFALSENONE: Bad signature for"
                          " sender of bucket: {}".format(node))
                    return (False, None)
            else:
                if not result[1]['signature']:
                    logger(self.sourceNode,
                          "RETFALSENONE: Signature not present"
                          " for sender of bucket: {}".format(node))
                return (False, None)
        else:
            logger(self.sourceNode,
                  "RETFALSENONE: No response from {},"
                  " removing from bucket".format(node))
            self.router.removeContact(node)
        return (False, None)

    def handleSignedPingResponse(self, result, node, challenge):
        """
        Handler for signed ping responses.
        `result` contains an array
            Element 0 contains ??
            Element 1 contains a dict with message fields.
        Return None on error.
        Return identity of ping response if signature is valid.
        """
        logger(self.sourceNode,"handleSignedPingResponse,result={}, node={}, challenge={}".format(result, node, challenge))
        address = (nodeToAsk.ip, nodeToAsk.port)
        logger(self.sourceNode, "handleSignedPingResponse {}".format(str(result)))
        if result[0]:
            if "NACK" in result[1]:
                return self.handleSignedNACKResponse(result,
                                                    node,
                                                    challenge)
            elif 'id' in result[1] and 'signature' in result[1]:
                if result[1]['id'] != node.id:
                    logger(self.sourceNode,
                          "RETNONE: Pong ID return "
                          "mismatch for {}".format(node))
                    return None
                nodeIdHex = node.id.encode('hex').upper()
                cert_stored = self.searchForCertificate(nodeIdHex)
                if cert_stored == None:
                    logger(self.sourceNode,
                          "RETNONE: Certificate for sender of pong: {} "
                          "not present in store".format(node))
                    return None
                try: 
                    self.runtime_credentials.verify_signed_data_from_certstring(
                                                                        cert_stored,
                                                                        result[1]['signature'],
                                                                        payload,
                                                                        certificate.TRUSTSTORE_TRANSPORT)
                    self.router.addContact(node)
                    return result[1]['id']
                except:
                    logger(self.sourceNode,
                          "RETNONE: Bad signature for sender"
                          " of pong: {}".format(node))
                    return None
            else:
                logger(self.sourceNode,
                      "RETNONE: Signature not present for sender"
                      " of pong: {}".format(node))
                return None
        else:
            logger(self.sourceNode,
                  "RETNONE: No pong from {}, removing"
                  " from bucket".format(node))
            self.router.removeContact(node)
        return None

    def handleSignedStoreResponse(self, result, node, challenge):
        """
        If we get a response and correctly signed challenge, add
        the node to the routing table.  If we get no response,
        make sure it's removed from the routing table.
        """
        logger(self.sourceNode,"handleSignedStoreResponse,result={}, node={}, challenge={}".format(result, node, challenge))
        logger(self.sourceNode, "handleSignedStoreResponse {}".format(str(result)))
        if result[0]:
            if "NACK" in result[1]:
                return (False, self.handleSignedNACKResponse(result,
                                                            node,
                                                            challenge))
            nodeIdHex = node.id.encode('hex').upper()
            cert_stored = self.searchForCertificate(nodeIdHex)
            if cert_stored == None:
                logger(self.sourceNode,
                "RETFALSENONE: Certificate for sender of store confirmation: {}"
                " not present in store".format(node))
                return (False, None)
            try: 
                self.runtime_credentials.verify_signed_data_from_certstring(
                                                                    cert_stored,
                                                                    result[1],
                                                                    challenge,
                                                                    certificate.TRUSTSTORE_TRANSPORT)
                self.router.addContact(node)
                logger(self.sourceNode, "handleSignedStoreResponse - finished OK")
                return (True, True)
            except:
                logger(self.sourceNode,
                      "RETFALSENONE: Bad signature for sender of store"
                      " confirmation: {}".format(node))
                return (False, None)
        else:
            logger(self.sourceNode,
                  "RETFALSENONE: No store confirmation from {},"
                  " removing from bucket".format(node))
            self.router.removeContact(node)
        return (False, None)

    def handleSignedValueResponse(self, result, node, challenge):
        logger(self.sourceNode,"handleSignedValueResponse,result={}, node={}, challenge={}".format(result, node, challenge))
        logger(self.sourceNode, "handleSignedValueResponse {}".format(str(result)))
        if result[0]:
            if "NACK" in result[1]:
                return (False, self.handleSignedNACKResponse(result,
                                                            node,
                                                            challenge))
            elif 'bucket' in result[1]:
                return self.handleSignedBucketResponse(result,
                                                      node,
                                                      challenge)
            elif 'value' in result[1] and 'signature' in result[1]:
                nodeIdHex = node.id.encode('hex').upper()
                cert_stored = self.searchForCertificate(nodeIdHex)
                if cert_stored == None:
                    logger(self.sourceNode,
                          "RETFALSENONE: Certificate for sender of value response: {}"
                          " not present in store".format(node))
                    return (False, None)
                try: 
                    self.runtime_credentials.verify_signed_data_from_certstring(
                                                                        cert_stored,
                                                                        result[1]['signature'],
                                                                        challenge,
                                                                        certificate.TRUSTSTORE_TRANSPORT)
                    self.router.addContact(node)
                    return result
                except:
                    logger(self.sourceNode,
                          "RETFALSENONE: Bad signature for sender of "
                          "value response: {}".format(node))
                    return (False, None)
            else:
                logger(self.sourceNode,
                      "RETFALSENONE: Signature not present for sender "
                      "of value response: {}".format(node))
                return (False, None)
        else:
            logger(self.sourceNode,
                  "No value response from {}, "
                  "removing from bucket".format(node))
            self.router.removeContact(node)
        return (False, None)

    def handleSignedNACKResponse(self, result, node, challenge):
        logger(self.sourceNode,"handleSignedNACKResponse, result={}, node={}, challenge={}".format(result, node, challenge))
        address = (nodeToAsk.ip, nodeToAsk.port)
        nodeIdHex = node.id.encode('hex').upper()
        cert_stored = self.searchForCertificate(nodeIdHex)
        if cert_stored == None:
            logger(self.sourceNode,
                  "Certificate for sender of NACK: {} "
                  "not present in store".format(node))
        if "NACK" in result[1]:
            logger(self.sourceNode,
                  "NACK in Value response")
            try:
                self.runtime_credentials.verify_signed_data_from_certstring(
                                                                    cert_stored,
                                                                    result[1]['signature'],
                                                                    challenge,
                                                                    certificate.TRUSTSTORE_TRANSPORT)
                self.callPing(node, self.getOwnCert())
                logger(self.sourceNode, "Certificate sent!")
            except:
                logger(self.sourceNode,
                      "Bad signature for sender "
                      "of NACK: {}".format(node))
        logger(self.sourceNode, "RETNONE: handleSignedNACKResponse")
        return None


    #####################
    # RPC Functions     #
    #####################

    def rpc_store(self, sender, nodeid, key, value, challenge, signature):
        logger(self.sourceNode,"rpc_store sender=%s, source=%s, key=%s, value=%s" % (sender, nodeid, base64.b64encode(key), str(value)))
        source = Node(nodeid, sender[0], sender[1])
        logger(self.sourceNode,
              "rpc_store {} ".format(str(sender)))
        nodeIdHex = nodeid.encode('hex').upper()
        cert_stored = self.searchForCertificate(nodeIdHex)
        if cert_stored == None:
            try:
                signature = self.runtime_credentials.sign_data(challenge)
            except:
                logger(self.sourceNode, "RETNONE: Failed make signature for store")
                return None
            logger(self.sourceNode,
                  "Certificate for {} not "
                  "found in store".format(source))
            return {'NACK' : None, "signature" : signature}
        else:
            try:
                sourceNodeIdHex = self.sourceNode.id.encode('hex').upper()
                payload = "{}{}".format(sourceNodeIdHex, challenge)
                self.runtime_credentials.verify_signed_data_from_certstring(
                                                                    cert_stored,
                                                                    signature,
                                                                    payload,
                                                                    certificate.TRUSTSTORE_TRANSPORT)
            except:
                logger(self.sourceNode,
                      "RETNONE: Bad signature for sender of "
                      "store request: {}".format(source))
                return None
            try:
                self.router.addContact(source)
            except Exception as err:
                _log.error("Failed to add contact to router, err={}".format(err))
            self.storage[key] = value
            try:
                signature = self.runtime_credentials.sign_data(challenge)
            except:
                logger(self.sourceNode,
                      "RETNONE: Signing of rpc_store failed")
                return None
            logger(self.sourceNode, "Signing of rpc_store success")
            return signature

    def rpc_append(self, sender, nodeid, key, value, challenge, signature):
        logger(self.sourceNode,"rpc_value:\n\tsender={}nodeid={}\n\tkey={}\n\tvalue={}".format(sender, nodeid, key, value))
        source = Node(nodeid, sender[0], sender[1])
        logger(self.sourceNode, "rpc_append {} ".format(str(sender)))
        nodeIdHex = nodeid.encode('hex').upper()
        cert_stored = self.searchForCertificate(nodeIdHex)
        if cert_stored == None:
            try:
                signature = self.runtime_credentials.sign_data(challenge)
            except:
                logger(self.sourceNode, "RETNONE: Failed make signature for append")
                return None
            logger(self.sourceNode,
                  "Certificate for {} not "
                  "found in store".format(source))
            return {'NACK' : None, "signature" : signature}
        else:
            try:
                sourceNodeIdHex = self.sourceNode.id.encode('hex').upper()
                payload = "{}{}".format(sourceNodeIdHex, challenge)
                self.runtime_credentials.verify_signed_data_from_certstring(
                                                                    cert_stored,
                                                                    signature,
                                                                    payload,
                                                                    certificate.TRUSTSTORE_TRANSPORT)
            except:
                logger(self.sourceNode,
                      "RETNONE: Bad signature for sender of "
                      "append request: {}".format(source))
                return None
            self.router.addContact(source)
            try:
                pvalue = json.loads(value)
                self.set_keys.add(key)
                if key not in self.storage:
                    logger(self.sourceNode, "append key: %s not in storage set value: %s" %
                                            (base64.b64encode(key), pvalue))
                    self.storage[key] = value
                else:
                    old_value_ = self.storage[key]
                    old_value = json.loads(old_value_)
                    new_value = list(set(old_value + pvalue))
                    logger(self.sourceNode, "append key: %s old: %s add: %s new: %s" %
                                            (base64.b64encode(key), old_value, pvalue, new_value))
                    self.storage[key] = json.dumps(new_value)
            except:
                logger(self.sourceNode,"RETNONE: Trying to append something not a JSON coded list %s" % value, exc_info=True)
                return None
            try:
                signature = self.runtime_credentials.sign_data(challenge)
            except:
                logger(self.sourceNode,
                      "RETNONE: Signing of rpc_append failed")
                return None
            return signature



    def rpc_remove(self, sender, nodeid, key, value, challenge, signature):
        logger(self.sourceNode,"rpc_remove\n\tsender={}\n\tnodeid={}\n\tkey={}\n\tvalue={}".format(sender, nodeid, key, value))
        source = Node(nodeid, sender[0], sender[1])
        logger(self.sourceNode, "rpc_remove {} ".format(str(sender)))
        nodeIdHex = nodeid.encode('hex').upper()
        cert_stored = self.searchForCertificate(nodeIdHex)
        if cert_stored == None:
            try:
                signature = self.runtime_credentials.sign_data(challenge)
            except:
                logger(self.sourceNode, "RETNONE: Failed make signature for remove")
                return None
            logger(self.sourceNode,
                  "Certificate for {} not "
                  "found in store".format(source))
            return {'NACK' : None, "signature" : signature}
        else:
            try:
                sourceNodeIdHex = self.sourceNode.id.encode('hex').upper()
                payload = "{}{}".format(sourceNodeIdHex, challenge)
                self.runtime_credentials.verify_signed_data_from_certstring(
                                                                    cert_stored,
                                                                    signature,
                                                                    payload,
                                                                    certificate.TRUSTSTORE_TRANSPORT)
            except:
                logger(self.sourceNode,
                      "RETNONE: Bad signature for sender of "
                      "remove request: {}".format(source))
                return None
            self.router.addContact(source)
            try:
                pvalue = json.loads(value)
                self.set_keys.add(key)
                if key in self.storage:
                    old_value = json.loads(self.storage[key])
                    new_value = list(set(old_value) - set(pvalue))
                    self.storage[key] = json.dumps(new_value)
                    logger(self.sourceNode, "remove key: %s old: %s add: %s new: %s" %
                                            (base64.b64encode(key), old_value, pvalue, new_value))
            except:
                logger(self.sourceNode,"RETNONE: Trying to remove somthing not a JSON coded list %s" % value, exc_info=True)
                return None
            try:
                signature = self.runtime_credentials.sign_data(challenge)
            except:
                logger(self.sourceNode,
                      "RETNONE: Signing of rpc_remove failed")
                return None
            return signature

    def rpc_find_node(self, sender, nodeid, key, challenge, signature):
        logger(self.sourceNode,"rpc_find_node")
        nodeIdHex = nodeid.encode('hex').upper()
        logger(self.sourceNode,
              "finding neighbors of {} "
              "in local table".format(long(nodeIdHex, 16)))

        source = Node(nodeid, sender[0], sender[1])
        cert_stored = self.searchForCertificate(nodeIdHex)
        if cert_stored == None:
            try:
                signature = self.runtime_credentials.sign_data(challenge)
            except:
                logger(self.sourceNode, "RETNONE: Failed make signature for find node")
                return None
            logger(self.sourceNode,
                  "Certificate for {} not found "
                  "in store".format(source))
            return {'NACK' : None, "signature" : signature}
        else:
            try:
                sourceNodeIdHex = self.sourceNode.id.encode('hex').upper()
                payload = "{}{}".format(sourceNodeIdHex, challenge)
                self.runtime_credentials.verify_signed_data_from_certstring(
                                                                    cert_stored,
                                                                    signature,
                                                                    payload,
                                                                    certificate.TRUSTSTORE_TRANSPORT)
            except:
                logger(self.sourceNode,
                      "RETNONE: Bad signature for sender of "
                      "find_node: {}".format(source))
                return None
            self.router.addContact(source)
            node = Node(key)
            bucket = map(list, self.router.findNeighbors(node, exclude=source))
            try:
                signature = self.runtime_credentials.sign_data(challenge)
            except:
                logger(self.sourceNode,
                      "RETNONE: Signing of rpc_find_node failed")
                return None
            value = {'bucket': bucket, 'signature': signature}
            return value

    def rpc_find_value(self, sender, nodeid, key, challenge, signature, cert_str=None):
        """
        ???
        Verifying received `challenge` and `signature` using
        supplied signature or stored signature derived from `nodeid`.
        """
        logger(self.sourceNode,"rpc_find_value:\n\tsender={}nodeid={}\n\tkey={}".format(sender, nodeid, key))
        source = Node(nodeid, sender[0], sender[1])
        nodeIdHex = nodeid.encode('hex').upper()
        cert_stored = self.searchForCertificate(nodeIdHex)
        if cert_stored == None:
            sourceNodeIdHex = self.sourceNode.id.encode("hex").upper()

            if key == digest("{}cert".format(sourceNodeIdHex)) and \
                                                        cert_str != None:
            # If the senders certificate is not in store,
            # the only allowed action is to ask it for its certificate
                try:
                    #verify certificate chain
                    self.runtime_credentials.verify_certificate(cert_str, certificate.TRUSTSTORE_TRANSPORT)
                    id = dhtidhex_from_certstring(cert_str)
                    if id != nodeIdHex:
                        logger(self.sourceNode,
                              "RETNONE: Explicit certificate in find_value "
                              "from {} does not match nodeid".format(source))
                        return None
                    sourceNodeIdHex = self.sourceNode.id.encode('hex').upper()
                    payload = "{}{}".format(sourceNodeIdHex, challenge)
                    self.runtime_credentials.verify_signed_data_from_certstring(
                                                                    cert_str,
                                                                    signature,
                                                                    payload,
                                                                    certificate.TRUSTSTORE_TRANSPORT)
                    self.storeCert(cert_str, nodeIdHex)
                except:
                    logger(self.sourceNode,
                          "RETNONE: Invalid certificate "
                          "request: {}".format(source))
                    return None
            else:
                try:
                    signature = self.runtime_credentials.sign_data(challenge)
                except:
                    logger(self.sourceNode, "RETNONE: Failed make signature for find value")
                    return None
                logger(self.sourceNode,
                      "Certificate for {} not "
                      "found in store".format(source))
                return { 'NACK' : None, 'signature': signature}
        else:
            try:
                sourceNodeIdHex = self.sourceNode.id.encode('hex').upper()
                payload = "{}{}".format(sourceNodeIdHex, challenge)
                # Verifying stored certificate with signature.
                self.runtime_credentials.verify_signed_data_from_certstring(
                                                                    cert_stored,
                                                                    signature,
                                                                    payload,
                                                                    certificate.TRUSTSTORE_TRANSPORT)
            except:
                logger(self.sourceNode,
                      "RETNONE: Bad signature for sender of "
                      "find_value: {}".format(source))
                return None

        self.router.addContact(source)
        exists, value = self.storage.get(key, None)
        if not exists:
            logger(self.sourceNode,
                  "Key {} not in store, forwarding".format(key))
            return self.rpc_find_node(sender,
                                     nodeid,
                                     key,
                                     challenge,
                                     signature)
        else:
            try:
                signature = self.runtime_credentials.sign_data(challenge)
            except:
                logger(self.sourceNode,
                      "RETNONE: Signing of rpc_find_value failed")
                return None
            return { 'value': value, 'signature': signature }

    def rpc_ping(self, sender, nodeid, challenge, signature, cert_str=None):
        """
        This function is ???
        Verify `cert_str` certificate with CA from trust store.
        Verify `signature` of `challenge`.
        Store certificate if `cert_str` is verified.

        """
        logger(self.sourceNode,"rpc_ping:\n\tself.sourceNode.id={}\n\tsender={}\n\tnodeid={}\n\tchallenge={}\n\tsignature={}".format(self.sourceNode.id, sender, nodeid, challenge, signature.encode("hex")))
        source = Node(nodeid, sender[0], sender[1])
        nodeIdHex = nodeid.encode("hex").upper()
        if cert_str != None:
            try:
                self.runtime_credentials.verify_certificate(cert_str, certificate.TRUSTSTORE_TRANSPORT)
                # Ensure that the CA of the received certificate is trusted
                id = dhtidhex_from_certstring(cert_str)
                if id != nodeIdHex:
                    logger(self.sourceNode,
                          "RETNONE: Explicit certificate in ping from {} "
                          "does not match nodeid\n\tid from cert={}\n\tid in ping={}".format(source, id, nodeIdHex))
                    return None
                sourceNodeIdHex = self.sourceNode.id.encode('hex').upper()
                payload = "{}{}".format(sourceNodeIdHex, challenge)
                try:
                    self.runtime_credentials.verify_signed_data_from_certstring(
                                                                        cert_str,
                                                                        signature,
                                                                        payload,
                                                                        certificate.TRUSTSTORE_TRANSPORT)
                except Exception as err:
                    _log.error("Failed to verify signed ping, err={}\n\tcert={}\n\tsignature={}\n\tpayload={}".format(err,cert_str, signature.encode("hex"), payload))
                    raise
                if not self.certificateExists(nodeid):
                    self.storeCert(cert_str, nodeid)
                    self.transferKeyValues(source)
            except Exception as e:
                logger(self.sourceNode,
                      "RETNONE: Bad signature for sender of ping with "
                      "explicit certificate: {}, err={}".format(source, e))
                return None
        else:
            cert_stored = self.searchForCertificate(nodeIdHex)
            if cert_stored == None:
                try:
                    signature = self.runtime_credentials.sign_data(challenge)
                except:
                    logger(self.sourceNode,
                          "RETNONE: Failed make signature for ping")
                    return None
                logger(self.sourceNode,
                      "Certificate for {} not found "
                      "in store".format(source))
                return {'NACK' : None, "signature" : signature}
            else:
                try:
                    sourceNodeIdHex = self.sourceNode.id.encode('hex').upper()
                    payload = "{}{}".format(sourceNodeIdHex, challenge)
                    self.runtime_credentials.verify_signed_data_from_certstring(
                                                                        cert_stored,
                                                                        signature,
                                                                        payload,
                                                                        certificate.TRUSTSTORE_TRANSPORT)
                except:
                    logger(self.sourceNode,
                          "RETNONE: Bad signature for sender of "
                          "ping: {}".format(source))
                    return None
        try:
            signature = self.runtime_credentials.sign_data(challenge)
        except:
            logger(self.sourceNode, "RETNONE: Signing of rpc_ping failed")
            return None
        return { 'id': self.sourceNode.id, 'signature': signature }


    #####################
    # MISC              #
    #####################

    def certificateExists(self, id):
        """
        Returns however the certificate for a
        given id exists in the own DHT storage.
        """
        logger(self.sourceNode,"certificateExist")
        return digest("{}cert".format(id)) in self.storage

    def searchForCertificate(self, id):
        """
        Seaches the internal storage for the certificate
        for a node with a given ID. If only one certificate
        is found to match the ID, this is returned.
        If none or several is found, None is returned.
        """
        logger(self.sourceNode,"searchForCertificate")
        if digest("{}cert".format(id)) in self.storage:
            logger(self.sourceNode,"Certificate found in local storage")
            return list(self.storage.get(digest("{}cert".format(id))))[1]
        else:
            logger("Certificate not in local storage, search for it in persistant storage")
            nodeid = nodeid_from_dhtid(id)
            cert_str = self.runtime_credentials.get_certificate(cert_name=nodeid)
            return cert_str

    def _timeout(self, msgID):
        self._outstanding[msgID][0].callback((False, None))
        del self._outstanding[msgID]

    def transferKeyValues(self, node):
        """
        Given a new node, send it all the keys/values it
        should be storing. @param node: A new node that
        just joined (or that we just found out about).
        Process:
        For each key in storage, get k closest nodes.
        If newnode is closer than the furtherst in that
        list, and the node for this server is closer than
        the closest in that list, then store the key/value
        on the new node (per section 2.5 of the paper)
        """
        logger(self.sourceNode, "**** transfer key values ****")
        for key, value in self.storage.iteritems():
            keynode = Node(digest(key))
            neighbors = self.router.findNeighbors(keynode)
            if len(neighbors) > 0:
                newNodeClose = node.distanceTo(keynode) < neighbors[-1].distanceTo(keynode)
                thisNodeClosest = self.sourceNode.distanceTo(keynode) < neighbors[0].distanceTo(keynode)
            if len(neighbors) == 0 or (newNodeClose and thisNodeClosest):
                if key in self.set_keys:
                    self.callAppend(node, key, value)
                    return None
                else:
                    self.callStore(node, key, value)
                    return None

    def storeOwnCert(self, cert_str):
        """
        Stores the string representation of the nodes own
        certificate in the DHT.
        """
        logger(self.sourceNode,"storeOwnCert, certstr={}".format(cert_str))
        sourceNodeIdHex = self.sourceNode.id.encode("hex").upper()
        self.storage[digest("{}cert".format(sourceNodeIdHex))] = cert_str

    def storeCert(self, cert_str, id):
        """
        Takes a string representation of a PEM-encoded certificate and
        a nodeid as input. If the string is a valid PEM-encoded certificate
        and the CA of the the certificate is present in the trustedStore of
        this node, the certificate is stored in the DHT and written to disk
        for later use.
        """
        logger(self.sourceNode,"storeCert::\n\tcert_str={}\n\tid={}".format(cert_str, id))
        try:
            self.runtime_credentials.verify_certificate(cert_str, certificate.TRUSTSTORE_TRANSPORT)
        except:
            _log.error("The certificate for {} is not signed by a trusted CA!".format(id))
            logger(self.sourceNode,
                  "The certificate for {} is not signed "
                  "by a trusted CA!".format(id))
            return
        exists = self.storage.get(digest("{}cert".format(id)))
        if not exists[0]:
            logger(self.sourceNode,"cert not stored, let's store it")
            self.storage[digest("{}cert".format(id))] = cert_str
            store_path = self.runtime_credentials.store_others_cert(certstring=cert_str)
            logger(self.sourceNode,"storeCert: stored certificate at: {}".format(store_path))
        else:
            logger(self.sourceNode,"storeCert: certificate for {} is already in local store".format(id))

    def getOwnCert(self):
        """
        Retrieves the nodes own certificate from the nodes DHT-storage and
        returns it.
        """
        logger(self.sourceNode,"getOwnCert")
        sourceNodeIdHex = self.sourceNode.id.encode("hex").upper()
        return self.storage[digest("{}cert".format(sourceNodeIdHex))]

class AppendServer(Server):

    def __init__(self, ksize=20, alpha=3, id=None, storage=None, node_name=None, runtime_credentials=None):
        _log.debug("AppendServer::__init__:\n\tid={}\n\tnode_name={}\n\truntime_credentials={}".format(id, node_name, runtime_credentials))
        storage = storage or ForgetfulStorageFix()
        Server.__init__(self, ksize, alpha, id, storage=storage)
        self.set_keys=set([])
        self.node_name=node_name
        self.runtime_credentials=runtime_credentials
        self.protocol = KademliaProtocolAppend(self.node, self.storage, ksize, node_name=self.node_name, set_keys=self.set_keys, runtime_credentials=self.runtime_credentials)
        if kademlia_version != '0.5':
            _log.error("#################################################")
            _log.error("### EXPECTING VERSION 0.5 of kademlia package ###")
            _log.error("#################################################")

    def bootstrap(self, addrs):
        """
        Bootstrap the server by connecting to other known nodes in the network.

        Args:
            addrs: A `list` of (ip, port, cert) tuples.  Note that only IP addresses
                   are acceptable - hostnames will cause an error.
        """
        _log.debug("AppendServer::bootstrap, addrs={}".format(addrs))
        # if the transport hasn't been initialized yet, wait a second
        if self.protocol.transport is None:
            return task.deferLater(reactor,
                                    1,
                                    self.bootstrap,
                                    addrs)

        def initTable(results, challenge, id):
            _log.debug("initTable")
            nodes = []
            for addr, result in results.items():
                ip = addr[0]
                port = addr[1]
                if result[0]:
                    resultSign = result[1]['signature']
                    resultId = result[1]['id']
                    resultIdHex = resultId.encode('hex').upper()
                    data = self.protocol.certificateExists(resultIdHex)
                    if not data:
                        identifier = digest("{}cert".format(resultIdHex))
                        self.protocol.callCertFindValue(Node(resultId,
                                                            ip,
                                                            port),
                                                       Node(identifier))
                    else:
                        cert_stored = self.protocol.searchForCertificate(resultIdHex)
                        try:
                            self.runtime_credentials.verify_signed_data_from_certstring(
                                                                        cert_stored,
                                                                        resultSign,
                                                                        challenge,
                                                                        certificate.TRUSTSTORE_TRANSPORT)
                        except:
                            logger(self.protocol.sourceNode, "Failed verification of challenge during bootstrap")
                        nodes.append(Node(resultId,
                                         ip,
                                         port))
            spider = NodeSpiderCrawl(self.protocol,
                                    self.node,
                                    nodes,
                                    self.ksize,
                                    self.alpha)
            return spider.find()

        ds = {}
        challenge = generate_challenge()
        id = None
        if addrs:
            data = addrs[0]
            addr = (data[0], data[1])
            cert_str = data[2]
            logger(self.protocol.sourceNode, "\n########### DOING BOOTSTRAP ###########")
            try:
                id = dhtidhex_from_certstring(cert_str)
                signature = self.runtime_credentials.sign_data("{}{}".format(id, challenge))
                ds[addr] = self.protocol.ping(addr,
                                             self.node.id,
                                             challenge,
                                             signature,
                                             self.protocol.getOwnCert())
                self.protocol.storeCert(cert_str, id)
            except Exception as err:
                logger(self.protocol.sourceNode, "Bootstrap failed, err={}".format(err))
            if not id:
                return deferredDict(ds)
            node = Node(id.decode("hex"), data[0], data[1])
            if self.protocol.router.isNewNode(node):
                return deferredDict(ds).addCallback(initTable,
                                                   challenge,
                                                   id)
        _log.debug("No addrs supplied")
        return deferredDict(ds)

    def append(self, key, value):
        """
        For the given key append the given list values to the set in the network.
        """
        _log.debug("append")
        dkey = digest(key)
        node = Node(dkey)

        def append_(nodes):
            _log.debug("append_")
            # if this node is close too, then store here as well
            if not nodes or self.node.distanceTo(node) < max([n.distanceTo(node) for n in nodes]):
                try:
                    pvalue = json.loads(value)
                    self.set_keys.add(dkey)
                    if dkey not in self.storage:
                        _log.debug("%s local append key: %s not in storage set value: %s" % (base64.b64encode(node.id), base64.b64encode(dkey), pvalue))
                        self.storage[dkey] = value
                    else:
                        old_value_ = self.storage[dkey]
                        old_value = json.loads(old_value_)
                        new_value = list(set(old_value + pvalue))
                        _log.debug("%s local append key: %s old: %s add: %s new: %s" % (base64.b64encode(node.id), base64.b64encode(dkey), old_value, pvalue, new_value))
                        self.storage[dkey] = json.dumps(new_value)
                except:
                    _log.debug("Trying to append something not a JSON coded list %s" % value, exc_info=True)
            ds = [self.protocol.callAppend(n, dkey, value) for n in nodes]
            return defer.DeferredList(ds).addCallback(self._anyRespondSuccess)

        nearest = self.protocol.router.findNeighbors(node)
        if len(nearest) == 0:
            self.log.warning("There are no known neighbors to set key %s" % key)
            _log.debug("There are no known neighbors to set key %s" % key)
            return defer.succeed(False)

        spider = NodeSpiderCrawl(self.protocol, node, nearest, self.ksize, self.alpha)
        return spider.find().addCallback(append_)

    def set(self, key, value):
        """
        Set the given key to the given value in the network.
        """
        _log.debug("setting '%s' = '%s' on network" % (key, value))
        dkey = digest(key)
        node = Node(dkey)

        def store(nodes):
            _log.debug("setting '%s' on %s" % (key, map(str, nodes)))
            # if this node is close too, then store here as well
            if not nodes or self.node.distanceTo(node) < max([n.distanceTo(node) for n in nodes]):
                self.storage[dkey] = value
            ds = [self.protocol.callStore(n, dkey, value) for n in nodes]
            return defer.DeferredList(ds).addCallback(self._anyRespondSuccess)

        nearest = self.protocol.router.findNeighbors(node)
        if len(nearest) == 0:
            _log.warning("There are no known neighbors to set key %s" % key)
            return defer.succeed(False)
        spider = NodeSpiderCrawl(self.protocol, node, nearest, self.ksize, self.alpha)
        return spider.find().addCallback(store)

    def get(self, key):
        """
        Get a key if the network has it.

        Returns:
            :class:`None` if not found, the value otherwise.
        """
        dkey = digest(key)
        _log.debug("Server:get %s" % base64.b64encode(dkey))
        # if this node has it, return it
        exists, value = self.storage.get(dkey)
        if exists:
            return defer.succeed(value)
        node = Node(dkey)
        nearest = self.protocol.router.findNeighbors(node)
        if len(nearest) == 0:
            self.log.warning("There are no known neighbors to get key %s" % key)
            return defer.succeed(None)
        spider = ValueSpiderCrawl(self.protocol, node, nearest, self.ksize, self.alpha)
        return spider.find()

    def remove(self, key, value):
        """
        For the given key remove the given list values from the set in the network.
        """
        dkey = digest(key)
        node = Node(dkey)
        _log.debug("Server:remove %s" % base64.b64encode(dkey))

        def remove_(nodes):
            # if this node is close too, then store here as well
            if not nodes or self.node.distanceTo(node) < max([n.distanceTo(node) for n in nodes]):
                try:
                    pvalue = json.loads(value)
                    self.set_keys.add(dkey)
                    if dkey in self.storage:
                        old_value = json.loads(self.storage[dkey])
                        new_value = list(set(old_value) - set(pvalue))
                        self.storage[dkey] = json.dumps(new_value)
                        _log.debug("%s local remove key: %s old: %s remove: %s new: %s" % (base64.b64encode(node.id), base64.b64encode(dkey), old_value, pvalue, new_value))
                except:
                    _log.debug("Trying to remove somthing not a JSON coded list %s" % value, exc_info=True)
            ds = [self.protocol.callRemove(n, dkey, value) for n in nodes]
            return defer.DeferredList(ds).addCallback(self._anyRespondSuccess)

        nearest = self.protocol.router.findNeighbors(node)
        if len(nearest) == 0:
            self.log.warning("There are no known neighbors to set key %s" % key)
            return defer.succeed(False)

        spider = NodeSpiderCrawl(self.protocol, node, nearest, self.ksize, self.alpha)
        return spider.find().addCallback(remove_)

    def get_concat(self, key):
        """
        Get a key if the network has it. Assuming it is a list that should be combined.

        @return: C{None} if not found, the value otherwise.
        """
        _log.debug("get_concat")
        dkey = digest(key)
        # Always try to do a find even if we have it, due to the concatenation of all results
        exists, value = self.storage.get(dkey)
        node = Node(dkey)
        nearest = self.protocol.router.findNeighbors(node)
        _log.debug("Server:get_concat key=%s, value=%s, exists=%s, nbr nearest=%d" % (base64.b64encode(dkey), value, 
                                                                                      exists, len(nearest)))
        if len(nearest) == 0:
            # No neighbors but we had it, return that value
            if exists:
                return defer.succeed(value)
            self.log.warning("There are no known neighbors to get key %s" % key)
            return defer.succeed(None)
        spider = ValueListSpiderCrawl(self.protocol, node, nearest, self.ksize, self.alpha,
                                      local_value=value if exists else None)
        return spider.find()


class SpiderCrawl(crawling.SpiderCrawl):
    def __init__(self, protocol, node, peers, ksize, alpha):
        """
        Create a new C{SpiderCrawl}er.

        Args:
            protocol: A :class:`~kademlia.protocol.KademliaProtocol` instance.
            node: A :class:`~kademlia.node.Node` representing the key we're looking for
            peers: A list of :class:`~kademlia.node.Node` instances that provide the entry point for the network
            ksize: The value for k based on the paper
            alpha: The value for alpha based on the paper
        """
        from kademlia.log import Logger
        self.protocol = protocol
        self.ksize = ksize
        self.alpha = alpha
        self.node = node
        # Changed from ksize to (ksize + 1) * ksize
        self.nearest = NodeHeap(self.node, (self.ksize+1) * self.ksize)
        self.lastIDsCrawled = []
        self.log = Logger(system=self)
        self.log.debug("creating spider with peers: %s" % peers)
        self.nearest.push(peers)


class NodeSpiderCrawl(SpiderCrawl, crawling.NodeSpiderCrawl):
    # Make sure that our SpiderCrawl __init__ gets called (crawling.NodeSpiderCrawl don't have __init__)
    pass


class ValueSpiderCrawl(SpiderCrawl, crawling.ValueSpiderCrawl):
    def __init__(self, protocol, node, peers, ksize, alpha):
        # Make sure that our SpiderCrawl __init__ gets called
        SpiderCrawl.__init__(self, protocol, node, peers, ksize, alpha)
        # copy crawling.ValueSpiderCrawl statement besides calling original SpiderCrawl.__init__
        self.nearestWithoutValue = NodeHeap(self.node, 1)

    def _nodesFound(self, responses):
        """
        Handle the result of an iteration in _find.
        """
        toremove = []
        foundValues = []
        for peerid, response in responses.items():
            response = crawling.RPCFindResponse(response)
            if not response.happened():
                toremove.append(peerid)
            elif response.hasValue():
                foundValues.append(response.getValue())
            else:
                peer = self.nearest.getNodeById(peerid)
                self.nearestWithoutValue.push(peer)
                self.nearest.push(response.getNodeList())
        self.nearest.remove(toremove)

        # Changed that first try to wait for alpha responses
        if len(foundValues) >= self.alpha: 
            return self._handleFoundValues(foundValues) 
        if self.nearest.allBeenContacted():
            if len(foundValues) > 0: 
                return self._handleFoundValues(foundValues) 
            else:
                return None

        return self.find()


class ValueListSpiderCrawl(ValueSpiderCrawl):

    def __init__(self, *args, **kwargs):
        self.local_value = kwargs.pop('local_value', None)
        super(ValueListSpiderCrawl, self).__init__(*args, **kwargs)

    def _nodesFound(self, responses):
        """
        Handle the result of an iteration in C{_find}.
        """
        toremove = []
        foundValues = []
        for peerid, response in responses.items():
            response = crawling.RPCFindResponse(response)
            if not response.happened():
                toremove.append(peerid)
            elif response.hasValue():
                foundValues.append((peerid, response.getValue()))
            else:
                peer = self.nearest.getNodeById(peerid)
                self.nearestWithoutValue.push(peer)
                self.nearest.push(response.getNodeList())
        _log.debug("_nodesFound nearestWithoutValue: %s, nearest: %s, toremove: %s" %
                    (self.nearestWithoutValue.getIDs(), self.nearest.getIDs(), toremove))
        self.nearest.remove(toremove)

        # Changed that first try to wait for alpha responses
        if len(foundValues) >= self.alpha: 
            return self._handleFoundValues(foundValues) 
        if self.nearest.allBeenContacted():
            if len(foundValues) > 0: 
                return self._handleFoundValues(foundValues) 
            else:
                # not found at neighbours!
                if self.local_value:
                    # but we had it
                    return self.local_value
                else:
                    return None

        return self.find()

    def _handleFoundValues(self, jvalues):
        """
        We got some values!  Exciting.  But lets combine them all.  Also,
        make sure we tell the nearest node that *didn't* have
        the value to store it.
        """
        # TODO figure out if we could be more cleaver in what values are combined
        value = None
        _set_op = True
        if self.local_value:
            jvalues.append((None, self.local_value))
        _log.debug("_handleFoundValues %s" % str(jvalues))
        if len(jvalues) != 1:
            args = (self.node.long_id, str(jvalues))
            _log.debug("Got multiple values for key %i: %s" % args)
            try:
                values = [(v[0], json.loads(v[1])) for v in jvalues]
                value_all = []
                for v in values:
                    value_all = value_all + v[1]
                value = json.dumps(list(set(value_all)))
            except:
                # Not JSON coded or list, probably trying to do a get_concat on none set-op data
                # Do the normal thing
                _log.debug("_handleFoundValues ********", exc_info=True)
                valueCounts = Counter([v[1] for v in jvalues])
                value = valueCounts.most_common(1)[0][0]
                _set_op = False
        else:
            key, value = jvalues[0]

        peerToSaveTo = self.nearestWithoutValue.popleft()
        if peerToSaveTo is not None:
            _log.debug("nearestWithoutValue %d" % (len(self.nearestWithoutValue)+1))
            if _set_op:
                d = self.protocol.callAppend(peerToSaveTo, self.node.id, value)
            else:
                d = self.protocol.callStore(peerToSaveTo, self.node.id, value)
            return d.addCallback(lambda _: value)
        # TODO if nearest does not contain the proper set push to it
        return value
