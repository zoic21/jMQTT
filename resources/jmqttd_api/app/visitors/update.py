from __future__ import annotations
from logging import getLogger
from typing import Union
from weakref import ref

from visitors.abstractvisitor import LogicVisitor, VisitableLogic
from visitors.register import RegisteringLogicVisitor
from visitors.unregister import UnregisteringLogicVisitor
from logics.broker import BrkLogic
from logics.cmd import CmdLogic
from logics.eq import EqLogic
from models.broker import BrkModel
from models.eq import EqModel
from models.unions import CmdModel


logger = getLogger('jmqtt.visitor.update')


# -----------------------------------------------------------------------------
class UpdatingLogicVisitor(LogicVisitor):
    def __init__(
        self,
        currentLogic: VisitableLogic,
        targetModel: Union[BrkModel, EqModel, CmdModel],
    ):
        self.currentLogic = currentLogic
        self.targetModel = targetModel

    async def visit_brk(self, e: BrkLogic) -> None:
        logger.trace('id=%s, updating brk', e.model.id)
        # Let's stop first MQTT Client (and Real Time)
        await e.stop()
        e.model = self.targetModel
        await e.start()
        logger.debug('id=%s, brk updated', e.model.id)

    async def visit_eq(self, e: EqLogic) -> None:
        logger.trace('id=%s, updating eq', e.model.id)
        if (
            # Handle unrecoverable change (move on another Brk)
            self.targetModel.configuration.eqLogic
            != e.model.configuration.eqLogic
        ):
            unreg = await UnregisteringLogicVisitor(e).unregister()
            unreg[0] = EqLogic(self.targetModel)
            for logic in unreg:
                await RegisteringLogicVisitor(logic).register()
            logger.debug(
                'id=%s, eq updated (brk change: %s->%s)',
                e.model.id,
                e.model.configuration.eqLogic,
                self.targetModel.configuration.eqLogic,
            )
            return

        # Otherwise do a simple model swap and update subscriptions:
        # Backup current model and set new in place
        oldModel = e.model
        e.model = self.targetModel
        # TODO Add/Del eq to brk topics/wildcards if auto_add_cmd has changed and isEnable
        # If isEnable has changed
        if oldModel.isEnable != self.targetModel.isEnable:
            brk = e.weakBrk()
            if self.targetModel.isEnable:
                # Now Enabled subscribe all info cmds
                for cmd in [v for v in e.cmd_i.values()]:
                    await brk.addCmd(cmd)
                # TODO Add eq to brk topics/wildcards if auto_add_cmd is now enabled
            else:
                # Now Disabled unsubscribe all info cmds
                for cmd in [v for v in e.cmd_i.values()]:
                    await brk.delCmd(cmd)
                # TODO Del eq to brk topics/wildcards if auto_add_cmd is now disabled
        logger.debug('id=%s, eq updated', e.model.id)

    async def visit_cmd(self, e: CmdLogic) -> None:
        logger.trace('id=%s, updating cmd', e.model.id)
        if (
            # Handle an action cmd change
            self.targetModel.type != 'info'
            # Handle unrecoverable changes (move on another Brk)
            or (
                (e.model.eqLogic_id != self.targetModel.eqLogic_id)
                and (e.model.eqLogic_id not in e.weakBrk().eqpts)
            )
        ):
            # Backup old Broker id if needed later
            oldBrk = e.weakBrk().model.id
            # Unregiseted and forget old Cmd Completely
            await UnregisteringLogicVisitor(e).unregister()
            # Create new Cmd from targetModel
            newCmd = CmdLogic(self.targetModel)
            # Register newCmd
            await RegisteringLogicVisitor(newCmd).register()

            if self.targetModel.type != 'info':
                logger.debug('id=%s, cmd updated (action cmd)', newCmd.model.id)
            else:
                logger.debug(
                    'id=%s, cmd updated (brk change: %s->%s)',
                    newCmd.model.id,
                    oldBrk,
                    newCmd.weakBrk().model.id,
                )
            return

        # Remove CmdLogic from EqLogic
        await e.weakEq().delCmd(e)
        # Remove CmdLogic from BrkLogic
        await e.weakBrk().delCmd(e)  # TODO Check if NEEDED? Done in Eq?
        # Change model in CmdLogic
        e.model = self.targetModel
        # Add CmdLogic in EqLogic
        await EqLogic.all[e.model.eqLogic_id].addCmd(e)
        # Set weakBrk in CmdLogic
        e.weakBrk = ref(e.weakEq().weakBrk())
        # Add CmdLogic in BrkLogic
        await e.weakBrk().addCmd(e)
        logger.debug('id=%s, cmd updated', e.model.id)

    async def update(self) -> None:
        await self.currentLogic.accept(self)