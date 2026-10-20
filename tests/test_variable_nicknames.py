from __future__ import annotations

import copy
import unittest

from PySide6.QtWidgets import QApplication

from genesis.app.main_window import MainWindow
from genesis.core.instrument.discovery import loadBuiltInInstruments
from genesis.core.instrument.registry import InstrumentRegistry
from genesis.core.job.model import JobModel
from genesis.core.job.variable_names import (
    canonicalExpression,
    defaultVariableNickname,
    displayExpression,
    makeVariableNicknames,
)
from genesis.core.runtime.expression_eval import compileExpression, evaluateExpression
from genesis.instruments.yokogawa7651.driver import Yokogawa7651Instrument
from genesis.ui.job_builder.job_builder_widget import JobBuilderWidget


class VariableNicknameTests(unittest.TestCase):
    _app: QApplication | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        registry = InstrumentRegistry()
        loadBuiltInInstruments(registry)
        self.builder = JobBuilderWidget(registry)
        self.addCleanup(self.builder.deleteLater)

    def _addInstrument(self, key: str = "b29xx") -> None:
        for index in range(self.builder.availableInstrumentList.count()):
            item = self.builder.availableInstrumentList.item(index)
            if item.data(256) == key:
                self.builder.availableInstrumentList.setCurrentRow(index)
                self.builder._onAddInstrumentClicked()
                return
        self.fail(f"Missing instrument {key}")

    def _rename(self, key: str, nickname: str) -> None:
        table = self.builder.variableMappingEditor.table
        for row in range(table.rowCount()):
            item = table.item(row, 2)
            if item.data(256) == key:
                item.setText(nickname)
                return
        self.fail(f"Missing variable {key}")

    def _configuredPlot(self) -> None:
        self._addInstrument()
        instrument = self.builder._instanceEditors[0]
        for key, checkbox in instrument._measurementCheckboxes:
            checkbox.setChecked(key == "senseCurrentA")
        sweep = self.builder._sweepEditors[0]
        sweep.setDefinition(
            {
                "mode": "1d",
                "outer": {
                    "instrumentId": "b29xx-1",
                    "key": "forceVoltageLevelV",
                    "start": 0,
                    "stop": 1,
                    "points": 3,
                },
            }
        )
        self.builder._onAddPlotClicked("scatter")
        self.builder._plotEditors[0].setDefinition(
            {
                "x": {
                    "type": "var",
                    "instrumentId": "b29xx-1",
                    "key": "forceVoltageLevelV",
                },
                "ySeries": [
                    {"type": "expr", "expr": "b29xx-1:senseCurrentA", "name": ""}
                ],
            }
        )

    def test_tab_order_and_all_declared_variables_are_mapped(self) -> None:
        self.assertEqual(
            [self.builder.sectionTabs.tabText(index) for index in range(4)],
            ["Instruments", "Variable Names", "Sweep", "Plots"],
        )
        self._addInstrument("hp4145b")
        instrument = self.builder._instanceEditors[0]
        expected = {
            field.key for field in instrument.instrumentType.getJobConfigFields()
        }
        expected.update(
            key
            for key, _label in instrument.instrumentType.getAvailableMeasurementSignals()
        )
        self.assertEqual(set(instrument.variableNicknames), expected)
        self.assertEqual(
            self.builder.variableMappingEditor.table.rowCount(), len(expected)
        )
        self.assertEqual(
            instrument.variableNicknames["smu1ForceVoltageLevelV"], "SMU1_V_set"
        )
        self.assertEqual(instrument.variableNicknames["smu1CurrentA"], "SMU1_I_meas")

    def test_nicknames_update_picker_expressions_and_preserve_identity(self) -> None:
        self._configuredPlot()
        plot = self.builder._plotEditors[0]
        before = copy.deepcopy(plot.toDefinition())
        self._rename("forceVoltageLevelV", "V_gate")
        self._rename("senseCurrentA", "I_drain")
        self.assertIn("V_gate", plot.scatterXVarCombo.currentText())
        self.assertEqual(plot._yRows[0]["expr"].text(), "b29xx-1:I_drain")
        self.assertEqual(before, plot.toDefinition())
        self.assertIn("I_drain", self.builder.plotsList.item(0).text())
        self.assertIn(
            "I_drain",
            [
                plot.yInsertVariableCombo.itemText(i)
                for i in range(plot.yInsertVariableCombo.count())
            ],
        )
        self.assertIsNone(plot.validateDefinition())

    def test_7651_defaults_distinguish_source_settings_and_readbacks(self) -> None:
        self._addInstrument("yokogawa7651")
        instrument = self.builder._instanceEditors[0]
        variables = instrument.getVariableDefinitions()
        expected = {
            field.key for field in Yokogawa7651Instrument.getJobConfigFields()
        } | {
            key
            for key, _label in Yokogawa7651Instrument.getAvailableMeasurementSignals()
        }
        self.assertEqual({key for key, _label, _role in variables}, expected)
        self.assertEqual(len(variables), len(expected))
        for key, nickname in {
            "forceVoltageLevelV": "V_set",
            "forceCurrentLevelA": "I_set",
            "sourceVoltageSettingV": "V_set_readback",
            "sourceCurrentSettingA": "I_set_readback",
            "voltageLimitV": "V_limit",
            "currentLimitA": "I_limit",
            "outputEnabled": "Output_on",
            "outputUnstable": "Output_unstable",
            "communicationError": "Command_error",
        }.items():
            self.assertEqual(instrument.variableNicknames[key], nickname)
        roles = {key: role for key, _label, role in variables}
        self.assertEqual(roles["sourceVoltageSettingV"], "Readback")
        self.assertEqual(roles["outputEnabled"], "Setting / Readback")
        self.assertEqual(roles["forceVoltageLevelV"], "Sweep")

    def test_7651_round_trip_keeps_config_sweep_and_plot_references(self) -> None:
        self._addInstrument("yokogawa7651")
        instrument = self.builder._instanceEditors[0]
        before = instrument.getValues()
        for key, checkbox in instrument._measurementCheckboxes:
            checkbox.setChecked(key in {"sourceVoltageSettingV", "outputEnabled"})
        self.builder._sweepEditors[0].setDefinition(
            {
                "mode": "1d",
                "outer": {
                    "instrumentId": "yokogawa7651-1",
                    "key": "forceVoltageLevelV",
                    "start": 0,
                    "stop": 1,
                    "points": 3,
                },
            }
        )
        self.builder._onAddPlotClicked("scatter")
        self.builder._plotEditors[0].setDefinition(
            {
                "x": {
                    "type": "var",
                    "instrumentId": "yokogawa7651-1",
                    "key": "forceVoltageLevelV",
                },
                "ySeries": [
                    {
                        "type": "expr",
                        "expr": "yokogawa7651-1:sourceVoltageSettingV",
                        "name": "",
                    }
                ],
            }
        )
        self._rename("forceVoltageLevelV", "V_bias")
        self._rename("sourceVoltageSettingV", "V_bias_readback")
        payload = self.builder._buildJobDefinition()
        saved = payload["instruments"][0]
        self.assertEqual(saved["config"], before["config"])
        self.assertEqual(saved["safeConfig"], before["safeConfig"])
        self.assertEqual(saved["transport"], "visa")
        self.assertEqual(saved["address"], before["address"])
        self.assertEqual(payload["sweeps"][0]["key"], "forceVoltageLevelV")
        self.assertEqual(
            payload["plots"][0]["ySeries"][0]["expr"],
            "yokogawa7651-1:sourceVoltageSettingV",
        )
        self.builder._loadJobDefinition(payload)
        self.assertEqual(self.builder._buildJobDefinition(), payload)
        plotEditor = self.builder._plotEditors[0]
        self.assertEqual(
            plotEditor._yRows[0]["expr"].text(), "yokogawa7651-1:V_bias_readback"
        )
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        window.currentJob = JobModel.fromJson(payload)
        window._setupPlotsFromJob(payload)
        plot = window._plotWidgetsByIndex[0]
        self.assertEqual(plot.ySeriesLabels, ["yokogawa7651-1:V_bias_readback"])
        window._onSampleEmitted(
            1.0,
            {
                "yokogawa7651-1": {
                    "forceVoltageLevelV": 0.5,
                    "sourceVoltageSettingV": 0.5,
                    "outputEnabled": 1.0,
                }
            },
        )
        self.assertEqual(plot._count, 1)
        self.assertAlmostEqual(
            plot._yBySeries["yokogawa7651-1:V_bias_readback"][0], 0.5
        )

    def test_insert_uses_nickname_but_saved_expression_uses_driver_key(self) -> None:
        self._configuredPlot()
        plot = self.builder._plotEditors[0]
        self._rename("senseCurrentA", "I_drain")
        edit = plot._yRows[0]["expr"]
        edit.clear()
        plot.yInsertVariableCombo.setCurrentIndex(
            plot.yInsertVariableCombo.findData("b29xx-1:senseCurrentA")
        )
        plot._insertScatterYToken()
        self.assertEqual(edit.text(), "b29xx-1:I_drain")
        definition = plot.toDefinition()
        expression = definition["ySeries"][0]["expr"]
        self.assertEqual(expression, "b29xx-1:senseCurrentA")
        compiled = compileExpression(expression)
        self.assertEqual(
            evaluateExpression(compiled, {("b29xx-1", "senseCurrentA"): 0.25}), 0.25
        )

    def test_save_load_and_legacy_defaults(self) -> None:
        self._configuredPlot()
        self._rename("senseCurrentA", "I_drain")
        payload = self.builder._buildJobDefinition()
        self.assertEqual(
            payload["instruments"][0]["variableNicknames"]["senseCurrentA"], "I_drain"
        )
        self.builder._loadJobDefinition(payload)
        self.assertEqual(
            self.builder._instanceEditors[0].variableNicknames["senseCurrentA"],
            "I_drain",
        )
        self.assertEqual(
            self.builder._plotEditors[0]._yRows[0]["expr"].text(), "b29xx-1:I_drain"
        )
        self.assertEqual(self.builder._buildJobDefinition()["plots"], payload["plots"])
        legacy = copy.deepcopy(payload)
        legacy["instruments"][0].pop("variableNicknames")
        self.builder._loadJobDefinition(legacy)
        self.assertEqual(
            self.builder._instanceEditors[0].variableNicknames["senseCurrentA"],
            "I_meas",
        )
        self.assertIsNone(self.builder._plotEditors[0].validateDefinition())

    def test_duplicate_invalid_and_key_collisions_do_not_change_mapping(self) -> None:
        self._addInstrument()
        instrument = self.builder._instanceEditors[0]
        before = dict(instrument.variableNicknames)
        for invalid in ["V_set", "", "two names", "2current", "senseVoltageV"]:
            self._rename("senseCurrentA", invalid)
            self.assertEqual(instrument.variableNicknames, before)
        self._rename("senseCurrentA", "漏极电流")
        self.assertEqual(instrument.variableNicknames["senseCurrentA"], "漏极电流")

    def test_clone_rename_remove_and_reset_preserve_per_instrument_names(self) -> None:
        self._addInstrument()
        self._rename("senseCurrentA", "I_drain")
        self.builder._onCloneInstrumentClicked()
        self.assertEqual(
            self.builder._instanceEditors[1].variableNicknames["senseCurrentA"],
            "I_drain",
        )
        self.builder._instanceEditors[0].idLineEdit.setText("gate")
        self.assertEqual(
            self.builder._instanceEditors[0].variableNicknames["senseCurrentA"],
            "I_drain",
        )
        mapping = self.builder.variableMappingEditor
        mapping.instrumentCombo.setCurrentIndex(1)
        self._rename("senseCurrentA", "I_other")
        self.assertEqual(
            self.builder._instanceEditors[0].variableNicknames["senseCurrentA"],
            "I_drain",
        )
        self.builder._onRemoveInstrumentClicked()
        self.assertEqual(mapping.instrumentCombo.count(), 1)
        self.assertEqual(
            self.builder._instanceEditors[0].variableNicknames["senseCurrentA"],
            "I_drain",
        )
        mapping.resetButton.click()
        self.assertEqual(
            self.builder._instanceEditors[0].variableNicknames["senseCurrentA"],
            "I_meas",
        )

    def test_expression_conversion_is_scoped_and_supports_chinese(self) -> None:
        names = {
            ("a", "senseCurrentA"): "漏极电流",
            ("a", "senseVoltageV"): "V",
            ("b", "senseVoltageV"): "V",
        }
        raw = "a:senseVoltageV / a:senseCurrentA + b:senseVoltageV"
        display = displayExpression(raw, names)
        self.assertEqual(display, "a:V / a:漏极电流 + b:V")
        self.assertEqual(canonicalExpression(display, names), raw)
        self.assertEqual(
            displayExpression("a:senseVoltageValue", names), "a:senseVoltageValue"
        )
        defaults = makeVariableNicknames([("one", "Voltage"), ("two", "Voltage")])
        self.assertEqual(len(set(defaults.values())), 2)
        self.assertEqual(defaultVariableNickname("forceVoltageLevelV"), "V_set")

    def test_runtime_scatter_axes_legend_and_sampling_use_nicknames(self) -> None:
        self._configuredPlot()
        self._rename("forceVoltageLevelV", "V_gate")
        self._rename("senseCurrentA", "I_drain")
        payload = self.builder._buildJobDefinition()
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        window.currentJob = JobModel.fromJson(payload)
        window._setupPlotsFromJob(payload)
        plot = window._plotWidgetsByIndex[0]
        self.assertEqual(plot.plotWidget.getAxis("bottom").labelText, "b29xx-1:V_gate")
        self.assertEqual(plot.ySeriesLabels, ["b29xx-1:I_drain"])
        window._latestValues = {
            ("b29xx-1", "forceVoltageLevelV"): 0.5,
            ("b29xx-1", "senseCurrentA"): 0.01,
        }
        expression = payload["plots"][0]["ySeries"][0]
        self.assertAlmostEqual(window._resolveAxisValue(0, "y:0", expression), 0.01)
        window._onSampleEmitted(
            1.0, {"b29xx-1": {"forceVoltageLevelV": 0.5, "senseCurrentA": 0.01}}
        )
        self.assertEqual(plot._count, 1)
        self.assertAlmostEqual(plot._yBySeries["b29xx-1:I_drain"][0], 0.01)

    def test_heatmap_aliases_and_color_label(self) -> None:
        self._configuredPlot()
        sweep = self.builder._sweepEditors[0]
        sweep.setDefinition(
            {
                "mode": "2d",
                "outer": {
                    "instrumentId": "b29xx-1",
                    "key": "forceVoltageLevelV",
                    "start": 0,
                    "stop": 1,
                    "points": 3,
                },
                "inner": {
                    "instrumentId": "b29xx-1",
                    "key": "forceCurrentLevelA",
                    "start": 0,
                    "stop": 0.1,
                    "points": 3,
                },
            }
        )
        self.builder._onAddPlotClicked("heatmap")
        plot = self.builder._plotEditors[-1]
        plot.setDefinition(
            {
                "renderMode": "heatmap",
                "x": {
                    "type": "var",
                    "instrumentId": "b29xx-1",
                    "key": "forceVoltageLevelV",
                },
                "heatmapY": {
                    "type": "var",
                    "instrumentId": "b29xx-1",
                    "key": "forceCurrentLevelA",
                },
                "ySeries": [
                    {"type": "expr", "expr": "b29xx-1:senseCurrentA", "name": ""}
                ],
            }
        )
        self._rename("senseCurrentA", "I_drain")
        self.assertIn("V_set", plot.heatmapXVarCombo.currentText())
        self.assertIn("I_set", plot.heatmapYVarCombo.currentText())
        self.assertEqual(plot.zExprLineEdit.text(), "b29xx-1:I_drain")
        payload = self.builder._buildJobDefinition()
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        window.currentJob = JobModel.fromJson(payload)
        window._setupPlotsFromJob(payload)
        heatmap = window._plotWidgetsByIndex[1]
        self.assertEqual(heatmap._xLabel, "b29xx-1:V_set")
        self.assertEqual(heatmap._yLabel, "b29xx-1:I_set")
        self.assertEqual(heatmap._zLabel, "b29xx-1:I_drain")
        window._onSampleEmitted(
            1.0,
            {
                "b29xx-1": {
                    "forceVoltageLevelV": 0.5,
                    "forceCurrentLevelA": 0.05,
                    "senseCurrentA": 0.01,
                }
            },
        )
        self.assertEqual(len(heatmap._zByKeyPair), 1)

    def test_runtime_legacy_defaults_and_custom_series_name(self) -> None:
        self._configuredPlot()
        payload = self.builder._buildJobDefinition()
        payload["instruments"][0].pop("variableNicknames")
        payload.pop("plotVariables")
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        window.currentJob = JobModel.fromJson(payload)
        window._setupPlotsFromJob(payload)
        plot = window._plotWidgetsByIndex[0]
        self.assertEqual(plot.plotWidget.getAxis("bottom").labelText, "b29xx-1:V_set")
        self.assertEqual(plot.ySeriesLabels, ["b29xx-1:I_meas"])
        self.assertEqual(
            window._seriesDisplayLabel(
                {
                    "type": "expr",
                    "expr": "b29xx-1:senseCurrentA",
                    "name": "Drain current",
                },
                1,
            ),
            "Drain current",
        )

    def test_time_axis_keeps_stable_reference_and_readable_name(self) -> None:
        self._addInstrument()
        self.builder._sweepEditors[0].setDefinition(
            {
                "mode": "1d",
                "outer": {
                    "instrumentId": "__time__",
                    "key": "time",
                    "start": 0,
                    "stop": 1,
                    "points": 3,
                },
            }
        )
        self.builder._onAddPlotClicked("scatter")
        plot = self.builder._plotEditors[0]
        self.assertEqual(plot.scatterXVarCombo.currentText(), "Time (s)")
        self.assertEqual(
            plot.toDefinition()["x"],
            {"type": "var", "instrumentId": "__time__", "key": "time"},
        )


if __name__ == "__main__":
    unittest.main()
