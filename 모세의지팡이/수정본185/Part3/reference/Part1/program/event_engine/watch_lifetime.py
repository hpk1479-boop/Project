"""Resident Watch registry; serialization happens only at explicit host save."""
from copy import deepcopy
from domain_clock import event_scope
from domain_memory import memory_scope
from .domain_support import plain
from .oz_processor import MessageCollector
from .watch_runtime import WatchMonitor


class WatchRuntime:
    def __init__(self,oz,config,memory):
        self.oz=oz;self.config=dict(config);self.memory=dict(memory)
        self.clock={};self.monitors={};self.collector=MessageCollector()
        with memory_scope(self.memory):
            self.sender=oz.DomainEventSender(config,transport=self.collector)
            self.controller=oz.GenericWatchController(self.sender,event_state={})
            self.controller._load_state()
        self.controller._save_state_locked=self.mark_dirty
        self.dirty=False
    def mark_dirty(self):self.dirty=True
    def __deepcopy__(self,memo):
        result=type(self)(self.oz,self.config,{})
        memo[id(self)]=result
        result.memory=deepcopy(self.memory,memo);result.clock=deepcopy(self.clock,memo)
        result.controller._watches=deepcopy(self.controller._watches,memo)
        result.controller._revision=self.controller._revision;result.dirty=self.dirty
        memo[id(self.controller)]=result.controller
        result.monitors=deepcopy(self.monitors,memo)
        return result
    def export_files(self):
        with memory_scope(self.memory):
            self.oz.GenericWatchController._save_state_locked(self.controller)
        return {'oz_generic_watch_state.json':self.memory['oz_generic_watch_state.json']}
    def process(self,event,board,command):
        symbol=event.payload['symbol'];self.collector.events.clear()
        with event_scope(event.source_time,'WATCH_CONDITIONS',self.clock),memory_scope(self.memory):
            if command:
                action=command.get('action')
                if action=='GENERIC_WATCH':self.controller.add(plain(command))
                elif action=='CANCEL_GENERIC':self.controller.cancel(plain(command))
                elif action=='RESET_ALL':self.controller.reset_all(request_chat_id=command.get('request_chat_id'))
            else:
                if symbol not in self.monitors:self.monitors[symbol]=WatchMonitor(symbol,self.controller)
                self.monitors[symbol].evaluate(board,event.source_time/1000)
        return self.collector.events
