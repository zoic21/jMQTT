from __future__ import annotations
from logging import getLogger
from typing import List, Union
from weakref import ref, WeakValueDictionary

from logics.abstractvisitor import LogicVisitor, VisitableLogic
from logics.broker import BrkLogic
from logics.cmd import CmdLogic
from logics.eq import EqLogic


# -----------------------------------------------------------------------------
class RegisteringLogicVisitor(LogicVisitor):
    def __init__(self):
        self.logger = getLogger('jmqtt.visitor.reg')

    async def visit_brk(self, e: BrkLogic) -> None:
        self.logger.trace('id=%s, registering brk', e.model.id)
        # Add BrkLogic in brkLogic table
        BrkLogic.all[e.model.id] = e
        await e.start()
        self.logger.debug('id=%s, brk registered', e.model.id)

    async def visit_eq(self, e: EqLogic) -> None:
        self.logger.trace('id=%s, registering eq', e.model.id)
        brkId = e.model.configuration.eqLogic
        # If BrkLogic is not found
        if brkId not in BrkLogic.all:
            self.logger.warning(
                'id=%s, eq disregarded: BrkId=%s not found', e.model.id, brkId
            )
            return
        # Cleanup EqLogic just in case
        e.cmd_i.clear()
        e.cmd_a.clear()
        # Add the reference to BrkLogic
        e.weakBrk = ref(BrkLogic.all[brkId])
        # Add in eqLogics
        EqLogic.all[e.model.id] = e
        # Add EqLogic in BrkLogic eqLogics list
        e.weakBrk().eqpts[e.model.id] = e
        self.logger.debug('id=%s, brk registered', e.model.id)

    async def visit_cmd(self, e: CmdLogic) -> None:
        self.logger.trace('id=%s, registering cmd', e.model.id)
        # Get parent eqLogic
        if e.model.eqLogic_id in EqLogic.all:
            # Parent is an EqLogic
            eq = EqLogic.all[e.model.eqLogic_id]
            # Add the reference to EqLogic and BrkLogic
            e.weakEq = ref(eq)
            e.weakBrk = ref(eq.weakBrk())
        elif e.model.eqLogic_id in BrkLogic.all:
            # Parent is a BrkLogic
            eq = BrkLogic.all[e.model.eqLogic_id]
            # Add the reference to EqLogic and BrkLogic
            e.weakEq = ref(eq)
            e.weakBrk = ref(eq)
        else:  # Could not find a parent
            self.logger.warning(
                'id=%s, cmd disregarded: EqId=%s not found', e.model.id, e.model.eqLogic_id
            )
            return
        # Only add in CmdLogic if found a parent
        CmdLogic.all[e.model.id] = e
        # Add CmdLogic ref in EqLogic/BrkLogic
        if e.model.type == 'info':
            eq.cmd_i[e.model.id] = e
        else:
            eq.cmd_a[e.model.id] = e
            # self.logger.debug('id=%s, cmd disregarded: not an info', e.model.id)
        # Finish here if eq is not enabled
        if not eq.model.isEnable:
            self.logger.debug('id=%s, cmd registered, but is not enabled', e.model.id)
            return
        # Insert path in info topic tree
        if e.model.type != 'info':
            self.logger.debug('id=%s, cmd registered, but is an action', e.model.id)
            return
        topic = e.model.configuration.topic
        # TODO Check if topic is subscribable
        # if isBadTopicFilter(e.subscription):
        if topic == '':
            self.logger.info(
                'id=%s, cmd registered, but topic "%s" is not subscribable',
                e.model.id, topic
            )
            return
        brk = e.weakBrk()
        # Add topic to BrkLogic if missing
        sub_needed = topic not in brk.topics
        if sub_needed:
            brk.topics[topic] = WeakValueDictionary()
        # Add CmdLogic to topics in BrkLogic
        brk.topics[topic][e.model.id] = e
        if sub_needed and brk.model.isEnable:
            await brk.subscribe(topic, 1)  # TODO Get QoS when Qos location is in cmd
        self.logger.debug('id=%s, cmd registered', e.model.id)

    @classmethod
    async def do(cls, e: VisitableLogic) -> None:
        self = cls()
        await e.accept(self)


# -----------------------------------------------------------------------------
class UnregisteringLogicVisitor(LogicVisitor):
    def __init__(self):
        self.logger = getLogger('jmqtt.visitor.unreg')
        self.result = []

    async def visit_brk(self, e: BrkLogic) -> None:
        self.logger.trace('id=%s, unregistering brk', e.model.id)
        # Let's stop first MQTT Client (and Real Time)
        await e.stop()
        # Then append the BrkLogic first to the result
        self.result.append(e)
        # Collect all directly linked CmdLogic, then EqLogic
        linked_cmd_eq = [v for v in e.cmd_i.values()]
        linked_cmd_eq += [v for v in e.cmd_a.values()]
        linked_cmd_eq += [v for v in e.eqpts.values()]
        for eq in linked_cmd_eq:
            await eq.accept(self)
        # Cleanup BrkLogic just in case
        e.cmd_i.clear()
        e.cmd_a.clear()
        e.eqpts.clear()
        # Delete the BrkLogic from the registery
        del BrkLogic.all[e.model.id]
        self.logger.debug('id=%s, brk unregistered', e.model.id)

    async def visit_eq(self, e: EqLogic) -> None:
        self.logger.trace('id=%s, unregistering eq', e.model.id)
        # Append this EqLogic to the result
        self.result.append(e)
        # Call the visitor on each CmdLogic info linked directly to the Broker
        for cmd in [v for v in e.cmd_i.values()]:
            await cmd.accept(self)
        # Call the visitor on each CmdLogic action linked directly to the Broker
        for cmd in [v for v in e.cmd_a.values()]:
            await cmd.accept(self)
        # Cleanup EqLogic just in case
        e.cmd_i.clear()
        e.cmd_a.clear()
        # Remove this EqLogic from the BrkLogic
        del e.weakBrk().eqpts[e.model.id]
        # Remove BrkLogic weakref
        e.weakBrk = None
        # Delete the EqLogic from the registery
        del EqLogic.all[e.model.id]
        self.logger.debug('id=%s, eq unregistered', e.model.id)

    async def visit_cmd(self, e: CmdLogic) -> None:
        self.logger.trace('id=%s, unregistering cmd', e.model.id)
        # Append this CmdLogic to the result
        self.result.append(e)
        # Handle removal from BrkLogic
        brk = e.weakBrk()
        topic = e.model.configuration.topic
        # Remove topic from Broker
        if topic in brk.topics:
            # Check if CmdLogic is in topics
            if e.model.id in brk.topics[topic]:
                del brk.topics[topic][e.model.id]
            # Check if unsubscription is needed
            if len(brk.topics[topic]) == 0:
                await brk.unsubscribe(topic)
                del brk.topics[topic]
        # Handle removal from EqLogic
        eq = e.weakEq()
        # Remove CmdLogic ref in EqLogic/BrkLogic
        if e.model.type == 'info':
            if e.model.id in eq.cmd_i:
                del eq.cmd_i[e.model.id]
        else:
            if e.model.id in eq.cmd_a:
                del eq.cmd_a[e.model.id]
        # Remove BrkLogic/EqLogic weakref
        e.weakBrk = None
        e.weakEq = None
        # Delete the CmdLogic from the registery
        del CmdLogic.all[e.model.id]
        self.logger.debug('id=%s, cmd unregistered', e.model.id)

    @classmethod
    async def do(
        cls, e: List[Union[BrkLogic, EqLogic, CmdLogic]]
    ) -> List[Union[BrkLogic, EqLogic, CmdLogic]]:
        self = cls()
        await e.accept(self)
        return self.result


# -----------------------------------------------------------------------------
class UpdatingLogicVisitor(LogicVisitor):
    def __init__(self):
        self.logger = getLogger('jmqtt.visitor.update')

    async def visit_brk(self, e: BrkLogic) -> None:
        pass

    async def visit_eq(self, e: EqLogic) -> None:
        pass

    async def visit_cmd(self, e: CmdLogic) -> None:
        pass

    @classmethod
    async def do(
        cls,
        existing: VisitableLogic,
        model: Union[BrkModel, EqModel, CmdModel],
    ) -> None:
        # TODO Use this visitor to update the correct logic
        # self = cls()
        # await e.accept(self)

        # Get class of existing logic
        logic = existing.__class__
        # Unregister existing logic
        unreged = await UnregisteringLogicVisitor.do(existing)
        # And replace existing logic by the created logic from model
        unreged[0] = logic(model)
        # Register back each unregistered logics
        for inst in unreged:
            # With the register class method of the logics
            await RegisteringLogicVisitor.do(inst)


# -----------------------------------------------------------------------------
class PrintVisitor(LogicVisitor):
    def __init__(self):
        self.logger = getLogger('jmqtt.visitor.print')
        self.level = 0

    async def visit_brk(self, e: BrkLogic) -> None:
        self.logger.debug(
            '%s┌─►  BrkLogic id=%s, name=%s, enabled=%s',
            '│ ' * self.level,
            e.model.id,
            e.model.name,
            '1' if e.model.isEnable else '0',
        )

        for t in e.topics:
            self.logger.debug(
                '%s│      %s => %s',
                '│ ' * self.level,
                t,
                ' '.join([str(v.model.id) for v in e.topics[t].values()]),
            )
        self.level += 1
        linked_cmd_eq = [v for v in e.cmd_i.values()]
        linked_cmd_eq += [v for v in e.cmd_a.values()]
        linked_cmd_eq += [v for v in e.eqpts.values()]
        for eq in linked_cmd_eq:
            await eq.accept(self)
        self.level -= 1
        self.logger.debug('%s└%s', '│ ' * self.level, '─' * (50 - 2 * self.level - 1))

    async def visit_eq(self, e: EqLogic) -> None:
        self.logger.debug(
            '%s┌─►  EqLogic  id=%s, name=%s, enabled=%s',
            '│ ' * self.level,
            e.model.id,
            e.model.name,
            '1' if e.model.isEnable else '0'
        )
        self.level += 1
        for cmd in [v for v in e.cmd_i.values()]:
            await cmd.accept(self)
        for cmd in [v for v in e.cmd_a.values()]:
            await cmd.accept(self)
        self.level -= 1
        self.logger.debug('%s└%s', '│ ' * self.level, '─' * (50 - 2 * self.level - 1))

    async def visit_cmd(self, e: CmdLogic) -> None:
        if e.model.type == 'info':
            self.logger.debug(
                '%s - CmdLogic id=%s, name=%s, type=info, topic=%s, jsonPath=%s',
                '│ ' * self.level,
                e.model.id,
                e.model.name,
                e.model.configuration.topic,
                e.model.configuration.jsonPath,
            )
        else:
            self.logger.debug(
                '%s - CmdLogic id=%s, name=%s, type=%s, topic=%s',
                '│ ' * self.level,
                e.model.id,
                e.model.name,
                e.model.type,
                e.model.configuration.topic,
            )

    @classmethod
    async def do(cls, e: List[Union[BrkLogic, EqLogic, CmdLogic]]) -> None:
        self = cls()
        await e.accept(self)
