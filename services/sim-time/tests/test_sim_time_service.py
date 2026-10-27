import unittest

from fastapi import HTTPException

from kody_sim_time.sim_time_service import (
    SimulationControlAction,
    SimulationTimeStore,
    SimulationUpdateRate,
)


class SimulationTimeStoreTests(unittest.TestCase):
    def test_advances_only_while_running_and_requires_the_current_revision(self) -> None:
        clock = [100.0]
        store = SimulationTimeStore(clock=lambda: clock[0])

        initial = store.response()
        self.assertEqual(initial["revision"], "0")
        self.assertFalse(initial["running"])
        self.assertEqual(initial["status"], "stopped")
        self.assertEqual(initial["elapsedSeconds"], 0.0)
        self.assertEqual(initial["updatesPerSecond"], 1.0)
        self.assertIn("+00:00", initial["wallClockUtc"])

        running = store.apply_action(SimulationControlAction(action="start"), initial["revision"])
        self.assertEqual(running["revision"], "1")
        self.assertEqual(running["status"], "running")
        clock[0] += 12.5
        self.assertEqual(store.response()["elapsedSeconds"], 12.5)

        paused = store.apply_action(SimulationControlAction(action="pause"), running["revision"])
        self.assertEqual(paused["status"], "paused")
        clock[0] += 8
        self.assertEqual(store.response()["elapsedSeconds"], paused["elapsedSeconds"])

        with self.assertRaises(HTTPException) as context:
            store.apply_action(SimulationControlAction(action="resume"), "0")
        self.assertEqual(context.exception.status_code, 409)

    def test_updates_rate_with_current_revision_and_publishes_event(self) -> None:
        store = SimulationTimeStore()
        updated = store.update_rate(SimulationUpdateRate(updatesPerSecond=10), "0")

        self.assertEqual(updated["updatesPerSecond"], 10)
        self.assertEqual(updated["revision"], "1")
        self.assertEqual([event["type"] for event in store.events_since(0)], ["rate-changed"])

        with self.assertRaises(HTTPException) as context:
            store.update_rate(SimulationUpdateRate(updatesPerSecond=2), "0")
        self.assertEqual(context.exception.status_code, 409)

    def test_reset_returns_to_zero_and_updates_the_revision(self) -> None:
        clock = [100.0]
        store = SimulationTimeStore(clock=lambda: clock[0])
        running = store.apply_action(SimulationControlAction(action="start"), "0")
        clock[0] += 5
        reset = store.reset()

        self.assertEqual(reset["elapsedSeconds"], 0.0)
        self.assertFalse(reset["running"])
        self.assertEqual(reset["status"], "stopped")
        self.assertEqual(reset["revision"], "2")
        self.assertEqual(running["revision"], "1")

    def test_publishes_ordered_lifecycle_and_reset_events(self) -> None:
        clock = [100.0]
        store = SimulationTimeStore(clock=lambda: clock[0])

        started = store.apply_action(SimulationControlAction(action="start"), "0")
        paused = store.apply_action(SimulationControlAction(action="pause"), started["revision"])
        resumed = store.apply_action(SimulationControlAction(action="resume"), paused["revision"])
        stopped = store.apply_action(SimulationControlAction(action="stop"), resumed["revision"])
        reset = store.reset()

        events = store.events_since(0)
        self.assertEqual([event["type"] for event in events], ["started", "paused", "resumed", "stopped", "reset"])
        self.assertEqual([event["id"] for event in events], [1, 2, 3, 4, 5])
        self.assertTrue(events[0]["state"]["running"])
        self.assertEqual(events[1]["state"]["status"], "paused")
        self.assertEqual(events[3]["state"]["revision"], stopped["revision"])
        self.assertEqual(events[3]["state"]["status"], "stopped")
        self.assertEqual(events[4]["state"]["revision"], reset["revision"])
        self.assertEqual(events[4]["state"]["elapsedSeconds"], 0.0)
