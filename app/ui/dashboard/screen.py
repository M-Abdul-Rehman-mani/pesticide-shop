"""Date-filtered dashboard metrics and sales chart."""

from __future__ import annotations

from datetime import date
from typing import cast

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDateEdit,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.orm import Session, sessionmaker

from app.reports.report_service import (
    DailyFinancialPoint,
    DashboardMetrics,
    DateRange,
    ReportService,
    TopSellingModel,
)
from app.ui.widgets import MetricCard, show_error
from app.ui.workers import FunctionWorker, start_worker
from app.utils.formatting import format_money


class DashboardScreen(QWidget):
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        timezone: str,
        currency: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_factory = session_factory
        self._timezone = timezone
        self._currency = currency
        self._worker: FunctionWorker | None = None
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setObjectName("PageTitle")
        self.from_date = QDateEdit(QDate.currentDate())
        self.from_date.setCalendarPopup(True)
        self.to_date = QDateEdit(QDate.currentDate())
        self.to_date.setCalendarPopup(True)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        heading.addWidget(title)
        heading.addStretch()
        heading.addWidget(QLabel("From"))
        heading.addWidget(self.from_date)
        heading.addWidget(QLabel("To"))
        heading.addWidget(self.to_date)
        heading.addWidget(refresh)
        layout.addLayout(heading)
        card_layout = QGridLayout()
        names = (
            "Sales",
            "Profit",
            "Units Sold",
            "Current Inventory",
            "Low Stock Products",
            "Expiring in 90 Days",
            "Outstanding Payments",
        )
        self.cards = {name: MetricCard(name) for name in names}
        for index, card in enumerate(self.cards.values()):
            card_layout.addWidget(card, index // 5, index % 5)
        layout.addLayout(card_layout)
        self.figure = Figure(figsize=(10, 5), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas, 1)

    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        if self.cards["Sales"].value_label.text() == "—":
            self.refresh()

    def refresh(self) -> None:
        start = cast(date, self.from_date.date().toPython())
        end = cast(date, self.to_date.date().toPython())

        def operation() -> tuple[
            DashboardMetrics,
            list[DailyFinancialPoint],
            list[TopSellingModel],
            dict[str, int],
        ]:
            period = DateRange.local_days(start, end, self._timezone)
            with self._session_factory() as session:
                service = ReportService(session)
                return (
                    service.dashboard(period),
                    service.financial_by_day(period, self._timezone),
                    service.top_selling_models(period),
                    service.inventory_status(),
                )

        self._worker = start_worker(
            operation,
            succeeded=self._display,
            failed=lambda error: show_error(self, error),
        )

    def _display(self, result: object) -> None:
        metrics, chart_rows, top_models, inventory_status = cast(
            tuple[
                DashboardMetrics,
                list[DailyFinancialPoint],
                list[TopSellingModel],
                dict[str, int],
            ],
            result,
        )
        self.cards["Sales"].set_value(format_money(metrics.sales, self._currency))
        self.cards["Profit"].set_value(format_money(metrics.profit, self._currency))
        self.cards["Units Sold"].set_value(str(metrics.units_sold))
        self.cards["Current Inventory"].set_value(str(metrics.current_inventory))
        self.cards["Low Stock Products"].set_value(str(metrics.low_stock_products))
        self.cards["Expiring in 90 Days"].set_value(str(metrics.expiring_units))
        self.cards["Outstanding Payments"].set_value(
            format_money(metrics.outstanding_payments, self._currency)
        )
        self.figure.clear()
        sales_axes = self.figure.add_subplot(221)
        profit_axes = self.figure.add_subplot(222)
        top_axes = self.figure.add_subplot(223)
        stock_axes = self.figure.add_subplot(224)
        sales_axes.set_title("Sales by day")
        if chart_rows:
            x_values = [float(row.day.toordinal()) for row in chart_rows]
            labels = [row.day.strftime("%d-%b") for row in chart_rows]
            sales_axes.plot(
                x_values,
                [float(row.sales) for row in chart_rows],
                marker="o",
                color="#2f80ed",
                label="Sales",
            )
            sales_axes.set_xticks(x_values, labels, rotation=30, ha="right")
            sales_axes.legend(fontsize=8)
            sales_axes.grid(axis="y", alpha=0.25)
            profit_axes.plot(
                x_values,
                [float(row.profit) for row in chart_rows],
                marker="o",
                color="#27ae60",
                label="Profit",
            )
            profit_axes.set_xticks(x_values, labels, rotation=30, ha="right")
            profit_axes.legend(fontsize=8)
            profit_axes.grid(axis="y", alpha=0.25)
        else:
            for axes, message in (
                (sales_axes, "No sales"),
                (profit_axes, "No profit data"),
            ):
                axes.text(0.5, 0.5, message, ha="center", va="center")
                axes.set_axis_off()
        profit_axes.set_title("Gross profit")
        top_axes.set_title("Top-selling pesticide products")
        if top_models:
            names = [model.product for model in reversed(top_models)]
            top_axes.barh(names, [model.units for model in reversed(top_models)], color="#56ccf2")
            top_axes.tick_params(axis="y", labelsize=7)
        else:
            top_axes.text(0.5, 0.5, "No model sales", ha="center", va="center")
            top_axes.set_axis_off()
        stock_axes.set_title("Inventory status")
        if inventory_status:
            labels = [key.replace("_", " ").title() for key in inventory_status]
            stock_axes.bar(labels, list(inventory_status.values()), color="#9b51e0")
            stock_axes.tick_params(axis="x", labelrotation=35, labelsize=7)
        else:
            stock_axes.text(0.5, 0.5, "No inventory", ha="center", va="center")
            stock_axes.set_axis_off()
        self.canvas.draw_idle()
