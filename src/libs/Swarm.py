# -*- coding: utf8 -*-


import json, requests
from config import REDIS
from SpliceURL import Splice
from utils.public import logger, RedisConnection, ip_check
from .Base import BASE_SWARM_ENGINE_API

class MultiSwarmManager(BASE_SWARM_ENGINE_API):


    def __init__(self, port=2375, timeout=3):
        self.storage   = RedisConnection
        self.swarmKey  = REDIS["SwarmKey"]
        self.ActiveKey = REDIS["ActiveKey"]
        self.port      = port
        self.timeout   = timeout
        self.verify    = False
        self._swarm    = self._unpickle
        self._active   = self._unpickleActive

    def _pickle(self, data):
        """ 序列化所有数据写入存储 """
        res = self.storage.set(self.swarmKey, json.dumps(data))
        logger.info("pickle data, content is %s, write result is %s" %(data, res))
        return res

    @property
    def _unpickle(self):
        """ 反序列化信息取出所有数据 """
        res = self.storage.get(self.swarmKey)
        logger.info("unpickle data is %s" %res)
        if res:
            return json.loads(res)
        else:
            return []

    def _pickleActive(self, data):
        """ 序列化活跃集群数据写入存储 """
        res = self.storage.set(self.ActiveKey, json.dumps(data))
        logger.info("pickle active data, content is %s, write result is %s" %(data, res))
        return res

    @property
    def _unpickleActive(self):
        """ 反序列化信息取出活跃集群 """
        res = self.storage.get(self.ActiveKey)
        logger.info("init active data is %s" %res)
        if res:
            return json.loads(res)
        else:
            return {}

    def isActive(self, name):
        """ 判断某name的swarm集群是否为活跃集群 """
        return name == self._active.get("name")

    @property
    def getAcitve(self):
        """ 查询活跃集群 """
        return self._active

    def setActive(self, name):
        """ 设置活跃集群 """
        logger.info("setActive, request name that will set is %s" % name)

        if self.isActive(name):
            logger.info("The name of the request is already active, think it successfully")
        else:
            logger.info("The name of the request is not current active swarm, will update it to be active.")
            self._active = self.getOne(name)
            self._pickleActive(self._active)
            if self.isActive(name):
                logger.info("setActive, the request name sets it for active, successfully")
            else:
                logger.info("setActive, the request name sets it for active, but fail")
                return False
        return True

    def isMember(self, name):
        """ 查询某name的swarm集群是否在存储中 """
        res = name in [ _.get("name") for _ in self._swarm ]
        logger.info("check %s, is member? %s" %(name, res))
        return res

    def getOne(self, name):
        """ 查询某name的swarm集群信息 """

        if self.isMember(name):
            return ( _ for _ in self._swarm if _.get("name") == name ).next()
        else:
            logger.warn("get %s, but no data" %name)

    def getSwarm(self, checkState=False):
        """ 查询存储中所有Swarm集群信息(并检查健康状态) """

        logger.debug(self._swarm)
        logger.info("get and check state(%s) for all swarm cluster, start" %checkState)
        swarms = []
        for swarm in self._swarm:
            if checkState:
                swarm.update(state=self._checkSwarmHealth(self._checkSwarmLeader(swarm)))
            elif "state" in swarm:
                swarm.pop("state")
            manager=self._checkSwarmManager(self._checkSwarmLeader(swarm))
            if manager:
                swarm.update(manager=manager)
            swarms.append(swarm)
        return swarms

    def GET(self, get, checkState=False):

        res = {"msg": None, "code": 0}
        logger.info("get request, the query params is %s, get state is %s" %(get, checkState))

        if not isinstance(get, (str, unicode)) or not get:
            res.update(msg="GET: query params type error or none", code=-1010)
        else:
            get = get.lower()
            if get == "all":
                res.update(data=self.getSwarm(checkState))
            elif get == "active":
                res.update(data=self.getAcitve)
            elif get == "leader":
                res.update(data=self._checkSwarmLeader(self.getAcitve))
            else:
                if self.isMember(get):
                    res.update(data=self.getOne(get))
                else:
                    res.update(msg="No such swarm", code=-1011)

        logger.info(res)
        return res

    def POST(self, swarmName, swarmIp):
        """ add a swarm cluster into current, check, pickle. """

        res = {"msg": None, "code": 0}
        logger.debug("post a swarm cluster, name is %s, ip is %s, check ip is %s" %(swarmName, swarmIp, ip_check(swarmIp)))

        if not swarmName or not swarmIp or not ip_check(swarmIp):
            res.update(msg="POST: data params error", code=-1020)
        elif self.isMember(swarmName):
            res.update(msg="POST: swarm cluster already exists", code=-1021)
        else:
            #access node ip's info, and get all remote managers
            url   = Splice(netloc=swarmIp, port=self.port, path='/info').geturl
            swarm = dict(name=swarmName)
            logger.info("init a swarm cluter named %s, will get swarm ip info, that url is %s" %(swarmName, url))
            try:
                nodeinfo = requests.get(url, timeout=self.timeout, verify=self.verify).json()
                logger.debug("get swarm ip info, response is %s" %nodeinfo)
                swarm["manager"] = [ nodes["Addr"].split(":")[0] for nodes in nodeinfo["Swarm"]["RemoteManagers"] ]
            except Exception,e:
                logger.error(e, exc_info=True)
                res.update(msg="POST: access the node ip has exception", code=-1022)
            else:
                token = self._checkSwarmToken(self._checkSwarmLeader(swarm))
                swarm.update(managerToken=token.get('Manager'), workerToken=token.get('Worker'))
                self._swarm.append(swarm)
                self._pickle(self._swarm)
                res.update(success=True, code=0)
                logger.info("check all pass and added")

        logger.info(res)
        return res

    def DELETE(self, name):
        """ 删除当前存储中的群集 """

        res = {"msg": None, "code": 0}
        logger.info("the name that will delete is %s" %name)

        if name in ("leader", "active", "all"):
            res.update(msg="DELETE: name reserved for the system key words", code=-1031)

        elif self.isActive(name):
            res.update(msg="DELETE: not allowed to delete the active cluster", code=-1032)

        elif self.isMember(name):
            swarm = self.getOne(name)
            logger.info("Will delete swarm cluster is %s" %swarm)
            self._swarm.remove(swarm)
            if self.isMember(name):
                logger.info("Delete fail")
                res.update(success=False)
            else:
                logger.info("Delete successfully, pickle current swarm")
                self._pickle(self._swarm)
                res.update(success=True)

        else:
            res.update(msg="DELETE: this swarm cluster does not exist", code=-1030)

        logger.info(res)
        return res

    def PUT(self, name, setActive=False):
        """ 更新集群信息、设置活跃集群 """

        res = {"msg": None, "code": 0}
        logger.info("PUT request, setActive(%s), will set %s as active" %(setActive, name))

        if setActive:
            if name and self.isMember(name):
                res.update(success=self.setActive(name))
            else:
                res.update(msg="PUT: setActive, but no name param or name non-existent", code=-1040)
        else:
            pass

        logger.info(res)
        return res

