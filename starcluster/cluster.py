#!/usr/bin/env python
import os
import time
import socket

import ssh
import awsutils
import cluster_setup
import static
from utils import AttributeDict, print_timing
from spinner import Spinner
from logger import log

import boto

def get_cluster(**kwargs):
    """Factory for Cluster class"""
    return Cluster(**kwargs)

class Cluster(AttributeDict):
    def __init__(self,
            AWS_ACCESS_KEY_ID=None,
            AWS_SECRET_ACCESS_KEY=None,
            AWS_USER_ID=None,
            CLUSTER_PROFILE=None,
            CLUSTER_TAG=None,
            CLUSTER_DESCRIPTION=None,
            CLUSTER_SIZE=None,
            CLUSTER_USER=None,
            CLUSTER_SHELL=None,
            MASTER_IMAGE_ID=None,
            NODE_IMAGE_ID=None,
            INSTANCE_TYPE=None,
            AVAILABILITY_ZONE=None,
            KEYNAME=None,
            KEY_LOCATION=None,
            VOLUME=None,
            VOLUME_DEVICE=None,
            VOLUME_PARTITION=None,
            **kwargs):
        if CLUSTER_TAG is None:
            CLUSTER_TAG = time.strftime("%Y%m%d%H%M")
        self.update({
            'AWS_ACCESS_KEY_ID': AWS_ACCESS_KEY_ID,
            'AWS_SECRET_ACCESS_KEY': AWS_SECRET_ACCESS_KEY,
            'AWS_USER_ID': AWS_USER_ID,
            'CLUSTER_PROFILE':CLUSTER_PROFILE,
            'CLUSTER_TAG':CLUSTER_TAG,
            'CLUSTER_DESCRIPTION':CLUSTER_DESCRIPTION,
            'CLUSTER_SIZE':CLUSTER_SIZE,
            'CLUSTER_USER':CLUSTER_USER,
            'CLUSTER_SHELL':CLUSTER_SHELL,
            'MASTER_IMAGE_ID':MASTER_IMAGE_ID,
            'NODE_IMAGE_ID':NODE_IMAGE_ID,
            'INSTANCE_TYPE':INSTANCE_TYPE,
            'AVAILABILITY_ZONE':AVAILABILITY_ZONE,
            'KEYNAME':KEYNAME,
            'KEY_LOCATION':KEY_LOCATION,
            'VOLUME':VOLUME,
            'VOLUME_DEVICE':VOLUME_DEVICE,
            'VOLUME_PARTITION':VOLUME_PARTITION,
        })
        self.ec2 = awsutils.get_easy_ec2(
            AWS_ACCESS_KEY_ID = self.AWS_ACCESS_KEY_ID, 
            AWS_SECRET_ACCESS_KEY = self.AWS_SECRET_ACCESS_KEY
        )
        self.__instance_types = static.INSTANCE_TYPES
        self.__cluster_settings = static.CLUSTER_SETTINGS
        self.__available_shells = static.AVAILABLE_SHELLS
        self._security_group = static.SECURITY_GROUP_TEMPLATE % self.CLUSTER_TAG
        self._master_reservation = None
        self._node_reservation = None
        self._nodes = []

    @property
    def security_group(self):
        try:
            sg = self.ec2.conn.get_all_security_groups(
                groupnames=[self._security_group])[0]
            return sg
        except boto.exception.EC2ResponseError, e:
            pass

    @property
    def master_node(self):
        sgname=static.SECURITY_GROUP_TEMPLATE % self.CLUSTER_TAG
        sg = self.ec2.conn.get_all_security_groups(groupnames=[sgname])[0]
        return sg

    @property
    def nodes(self):
        if self._nodes is None:
            log.debug('self._nodes = %s' % self._nodes)
            sg = self.security_group
            if sg:
                self._nodes=sg.instances()
        return self._nodes

    def create_cluster(self):
        log.info("Launching a %d-node cluster..." % self.CLUSTER_SIZE)
        if self.MASTER_IMAGE_ID is None:
            self.MASTER_IMAGE_ID = self.NODE_IMAGE_ID
        log.info("Launching master node...")
        log.info("MASTER AMI: %s" % self.MASTER_IMAGE_ID)
        conn = self.ec2.conn
        master_response = conn.run_instances(image_id=self.MASTER_IMAGE_ID,
            instance_type=self.INSTANCE_TYPE,
            min_count=1, max_count=1,
            key_name=self.KEYNAME,
            security_groups=[static.MASTER_GROUP, self._security_group],
            placement=self.AVAILABILITY_ZONE)
        print master_response
        if self.CLUSTER_SIZE > 1:
            log.info("Launching worker nodes...")
            log.info("NODE AMI: %s" % self.NODE_IMAGE_ID)
            instances_response = conn.run_instances(image_id=self.NODE_IMAGE_ID,
                instance_type=self.INSTANCE_TYPE,
                min_count=max((self.CLUSTER_SIZE-1)/2, 1),
                max_count=max(self.CLUSTER_SIZE-1,1),
                key_name=self.KEYNAME,
                security_groups=[self._security_group],
                placement=self.AVAILABILITY_ZONE)
            print instances_response

    def is_ssh_up(self):
        for node in self.nodes:
            s = socket.socket()
            s.settimeout(0.25)
            try:
                s.connect((node.dns_name, 22))
                s.close()
            except socket.error:
                return False
        return True

    def is_cluster_up(self):
        """
        TODO: Create get_running_instances equivalent for use below
        """
        #running_instances = get_running_instances()
        if len(self.nodes) == self.CLUSTER_SIZE:
            if self.is_ssh_up():
                return True
            else:
                return False
        else:
            return False

    def attach_volume_to_master(self):
        log.info("Attaching volume to master node...")
        master_instance = get_master_instance()
        if master_instance is not None:
            attach_response = attach_volume_to_node(master_instance)
            log.debug("attach_response = %s" % attach_response)
            if attach_response is not None:
                while True:
                    attach_volume = get_volume()
                    if len(attach_volume) != 2:
                        time.sleep(5)
                        continue
                    vol = attach_volume[0]
                    attachment = attach_volume[1]
                    if vol[0] != 'VOLUME' or attachment[0] != 'ATTACHMENT':
                        return False
                    if vol[1] != attachment[1] != self.VOLUME:
                        return False
                    if vol[4] == "in-use" and attachment[5] == "attached":
                        return True
                    time.sleep(5)

    def ssh_to_node(self,node_number):
        nodes = get_external_hostnames()
        if len(nodes) == 0:
            log.info('No instances to connect to...exiting')
            return
        try:
            node = nodes[int(node_number)]
            log.info("Logging into node: %s" % node)
            if platform.system() != 'Windows':
                os.system('ssh -i %s root@%s' % (self.KEY_LOCATION, node))
            else:
                os.system('putty -ssh -i %s root@%s' % (self.KEY_LOCATION, node))
        except:
            log.error("Invalid node_number. Please select a node number from the output of starcluster -l")

    def ssh_to_master(self):
        master_node = self.master_node
        if master_node is not None:
            log.info("MASTER NODE: %s" % master_node)
            if platform.system() != 'Windows':
                os.system('ssh -i %s root@%s' % (self.KEY_LOCATION,
                                                 master_node.dns_name)) 
            else:
                os.system('putty -ssh -i %s root@%s' % (self.KEY_LOCATION,
                                                        master_node.dns_name))
        else: 
            log.info("No master node found...")

    def stop_cluster(self):
        resp = raw_input(">>> This will shutdown all EC2 instances. Are you sure (yes/no)? ")
        if resp == 'yes':
            running_instances = get_running_instances()
            if len(running_instances) > 0:
                if has_attach_volume():
                    detach_vol = detach_volume()
                    log.debug("detach_vol_response: \n%s" % detach_vol)
                log.info("Listing instances ...")
                list_instances()
                for instance in running_instances:
                    log.info("Shutting down instance: %s " % instance)
                log.info("Waiting for instances to shutdown ....")
                terminate_instances(running_instances)
                time.sleep(5)
                log.info("Listing new state of instances")
                list_instances(refresh=True)
            else:
                log.info('No running instances found, exiting...')
        else:
            log.info("Exiting without shutting down instances....")

    def stop_slaves(self):
        running_instances = get_running_instances()
        if len(running_instances) > 0:
            log.info("Listing instances...")
            list_instances(refresh=True)
            #exclude master node....
            running_instances=running_instances[1:len(running_instances)]
            for instance in running_instances:
                log.info("Shutting down slave instance: %s " % instance)
            log.info("Waiting for shutdown...")
            terminate_instances(running_instances)
            time.sleep(5)
            log.info("Listing new state of slave instances")
            list_instances(refresh=True)
        else:
            log.info("No running instances found, exiting...")

    @print_timing
    def start_cluster(self, create=True):
        log.info("Starting cluster...")
        if create:
            self.create_cluster()
        s = Spinner()
        log.log(logger.INFO_NO_NEWLINE, "Waiting for cluster to start...")
        s.start()
        while True:
            if self.is_cluster_up():
                s.stop()
                break
            else:  
                time.sleep(15)

        #if self.has_attach_volume():
            #self.attach_volume_to_master()

        master_node = self.master_node
        log.info("The master node is %s" % master_node)

        log.info("Setting up the cluster...")
        #cluster_setup.main(self.get_nodes())
            
        log.info("""

The cluster has been started and configured. ssh into the master node as root by running: 

$ starcluster sshmaster 

or as %(user)s directly:

$ ssh -i %(key)s %(user)s@%(master)s

        """ % {'master': master_node, 'user': self.CLUSTER_USER, 'key': self.KEY_LOCATION})

    def is_valid(self): 
        CLUSTER_SIZE = self.CLUSTER_SIZE
        KEYNAME = self.KEYNAME
        KEY_LOCATION = self.KEY_LOCATION
        conn = self.ec2.conn 
        if not self._has_all_required_settings():
            log.error('Please specify the required settings')
            return False
        if not self._has_valid_credentials():
            log.error('Invalid AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY combination. Please check your settings')
            return False
        if not self._has_keypair():
            log.error('Account does not contain a key with KEYNAME = %s. Please check your settings' % KEYNAME)
            return False
        if not os.path.exists(KEY_LOCATION):
            log.error('KEY_LOCATION=%s does not exist. Please check your settings' % KEY_LOCATION)
            return False
        elif not os.path.isfile(KEY_LOCATION):
            log.error('KEY_LOCATION=%s is not a file. Please check your settings' % KEY_LOCATION)
            return False
        if CLUSTER_SIZE <= 0:
            log.error('CLUSTER_SIZE must be a positive integer. Please check your settings')
            return False
        if not self._has_valid_zone():
            log.error('Your AVAILABILITY_ZONE setting is invalid. Please check your settings')
            return False
        if not self._has_valid_ebs_settings():
            log.error('EBS settings are invalid. Please check your settings')
            return False
        if not self._has_valid_image_settings():
            log.error('Your MASTER_IMAGE_ID/NODE_IMAGE_ID setting(s) are invalid. Please check your settings')
            return False
        if not self._has_valid_instance_type_settings():
            log.error('Your INSTANCE_TYPE setting is invalid. Please check your settings')
            return False
        if not self._has_valid_shell_setting():
            log.error('Your CLUSTER_SHELL setting %s is invalid. Please check your settings' % self.CLUSTER_SHELL)
        return True

    def _has_valid_shell_setting(self):
        CLUSTER_SHELL = self.CLUSTER_SHELL
        if not self.__available_shells.get(CLUSTER_SHELL):
            return False
        return True

    def _has_valid_image_settings(self):
        MASTER_IMAGE_ID = self.MASTER_IMAGE_ID
        NODE_IMAGE_ID = self.NODE_IMAGE_ID
        conn = self.ec2.conn
        try:
            image = conn.get_all_images(image_ids=[NODE_IMAGE_ID])[0]
        except boto.exception.EC2ResponseError,e:
            log.error('NODE_IMAGE_ID %s does not exist' % NODE_IMAGE_ID)
            return False
        if MASTER_IMAGE_ID is not None:
            try:
                master_image = conn.get_all_images(image_ids=[MASTER_IMAGE_ID])[0]
            except boto.exception.EC2ResponseError,e:
                log.error('MASTER_IMAGE_ID %s does not exist' % MASTER_IMAGE_ID)
                return False
        return True

    def _has_valid_zone(self):
        conn = self.ec2.conn
        AVAILABILITY_ZONE = self.AVAILABILITY_ZONE
        if AVAILABILITY_ZONE:
            try:
                zone = conn.get_all_zones()[0]
                if zone.state != 'available':
                    log.error('The AVAILABILITY_ZONE = %s is not available at this time')
                    return False
            except boto.exception.EC2ResponseError,e:
                log.error('AVAILABILITY_ZONE = %s does not exist' % AVAILABILITY_ZONE)
                return False
        return True

    def _has_valid_instance_type_settings(self):
        MASTER_IMAGE_ID = self.MASTER_IMAGE_ID
        NODE_IMAGE_ID = self.NODE_IMAGE_ID
        INSTANCE_TYPE = self.INSTANCE_TYPE
        instance_types = self.__instance_types
        conn = self.ec2.conn
        if not instance_types.has_key(INSTANCE_TYPE):
            log.error("You specified an invalid INSTANCE_TYPE %s \nPossible options are:\n%s" % (INSTANCE_TYPE,' '.join(instance_types.keys())))
            return False

        try:
            node_image_platform = conn.get_all_images(image_ids=[NODE_IMAGE_ID])[0].architecture
        except boto.exception.EC2ResponseError,e:
            node_image_platform = None

        instance_platform = instance_types[INSTANCE_TYPE]
        if instance_platform != node_image_platform:
            log.error('You specified an incompatible NODE_IMAGE_ID and INSTANCE_TYPE')
            log.error('INSTANCE_TYPE = %(instance_type)s is for a %(instance_platform)s \
    platform while NODE_IMAGE_ID = %(node_image_id)s is a %(node_image_platform)s platform' \
                        % { 'instance_type': INSTANCE_TYPE, 'instance_platform': instance_platform, \
                            'node_image_id': NODE_IMAGE_ID, 'node_image_platform': node_image_platform})
            return False
        
        if MASTER_IMAGE_ID is not None:
            try:
                master_image_platform = conn.get_all_images(image_ids=[MASTER_IMAGE_ID])[0].architecture
            except boto.exception.EC2ResponseError,e:
                master_image_platform = None
            if instance_platform != master_image_platform:
                log.error('You specified an incompatible MASTER_IMAGE_ID and INSTANCE_TYPE')
                log.error('INSTANCE_TYPE = %(instance_type)s is for a %(instance_platform)s \
    platform while MASTER_IMAGE_ID = %(master_image_id)s is a %(master_image_platform)s platform' \
                            % { 'instance_type': INSTANCE_TYPE, 'instance_platform': instance_platform, \
                                'image_id': MASETER_IMAGE_ID, 'master_image_platform': master_image_platform})
                return False
        
        return True

    def _has_valid_ebs_settings(self):
        #TODO check that VOLUME id exists
        VOLUME = self.VOLUME
        VOLUME_DEVICE = self.VOLUME_DEVICE
        VOLUME_PARTITION = self.VOLUME_PARTITION
        AVAILABILITY_ZONE = self.AVAILABILITY_ZONE
        conn = self.ec2.conn
        if VOLUME is not None:
            try:
                vol = conn.get_all_volumes(volume_ids=[VOLUME])[0]
            except boto.exception.EC2ResponseError,e:
                log.error('VOLUME = %s does not exist' % VOLUME)
                return False
            if VOLUME_DEVICE is None:
                log.error('Must specify VOLUME_DEVICE when specifying VOLUME setting')
                return False
            if VOLUME_PARTITION is None:
                log.error('Must specify VOLUME_PARTITION when specifying VOLUME setting')
                return False
            if AVAILABILITY_ZONE is not None:
                if vol.availabilityZone != AVAILABILITY_ZONE:
                    log.error('The VOLUME you specified is only available in region %(vol_zone)s, \
    however, you specified AVAILABILITY_ZONE = %(availability_zone)s\nYou need to \
    either change AVAILABILITY_ZONE or create a new volume in %(availability_zone)s' \
                                % {'vol_zone': vol.region.name, 'availability_zone': AVAILABILITY_ZONE})
                    return False
        return True

    def _has_all_required_settings(self):
        has_all_required = True
        for opt in self.__cluster_settings:
            requirements = self.__cluster_settings[opt]
            name = opt; required = requirements[1];
            if required and self.get(name) is None:
                log.warn('Missing required setting %s' % name)
                has_all_required = False
        return has_all_required

    def _has_valid_credentials(self):
        try:
            self.ec2.conn.get_all_instances()
            return True
        except boto.exception.EC2ResponseError,e:
            return False

    def validate_aws_or_exit(self):
        conn = self.ec2.conn
        if conn is None or not self._has_valid_credentials():
            log.error('Invalid AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY combination. Please check your settings')
            sys.exit(1)
        
    def validate_or_exit(self):
        if not self.is_valid():
            log.error('configuration error...exiting')
            sys.exit(1)

    def _has_keypair(self):
        KEYNAME = self.KEYNAME
        conn = self.ec2.conn
        try:
            keypair = conn.get_all_key_pairs(keynames=[KEYNAME])
            return True
        except boto.exception.EC2ResponseError,e:
            return False

if __name__ == "__main__":
    from starcluster.config import StarClusterConfig
    cfg = StarClusterConfig(); cfg.load()
    sc =  cfg.get_cluster('smallcluster')
    print sc.is_valid()
