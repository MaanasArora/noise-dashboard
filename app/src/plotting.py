"""
Classes for creating the plots on the dashboard.
"""
from src.utils import (
    COLUMN,
    HEATMAP_VALUE,
    filter_outliers,
    load_config,
    get_current_dir,
    Logging,
)
import os
import json
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from typing import Optional, List, Dict
from abc import abstractmethod
import pandas.api.types as ptype
from enum import StrEnum, auto

logger = Logging.get_console_logger()


class COLOR_ITEM(StrEnum):
    MIN = auto()
    MAX = auto()


class BasePlotter:
    """
    Base class for plotting - loads config.
    """

    def __init__(
        self, df: pd.DataFrame, bootstrap_template: str = None
    ) -> None:
        self._config = load_config()

        self._validate_data(df)
        self.df = df

        self.template = None
        self.template_name = bootstrap_template
        if self.template_name is not None:
            self.template = self._load_template(self.template_name)

        self.colors = self._set_colors()

    def _set_colors(self) -> Dict[COLOR_ITEM, str]:
        """
        Determine the main colors for the chart to show min/max measurements. Based on the template if provided or the config as a fallback.
        """
        if self.template is None:
            colors = {
                COLOR_ITEM.MIN: self._config["plot.colors"]["min"],
                COLOR_ITEM.MAX: self._config["plot.colors"]["max"],
            }
        else:
            colors = {
                COLOR_ITEM.MIN: self.template["layout"]["colorway"][0],
                COLOR_ITEM.MAX: self.template["layout"]["colorway"][1],
            }

        return colors

    def _set_background(self, fig: go.Figure) -> None:
        """
        Set background colors for the plot based on the config file.
        """
        fig.update_layout(
            paper_bgcolor=self._config["plot.colors"]["background"],
            plot_bgcolor="rgba(0, 0, 0, 0)",
        )

    def _set_title_size(self, fig: go.Figure) -> None:
        """
        Set title size based on config.
        """
        fig.update_layout(
            title=dict(
                font=dict(size=int(self._config["plot.text"]["title_size"]))
            ),
        )

    @staticmethod
    def _load_template(name: str) -> dict:
        """
        Load the Plotly template from file for a Bootstrap theme by its name.
        """
        name = name.lower()
        file_name = name + ".json"
        file_path = os.path.join(
            get_current_dir(__file__), "templates", file_name
        )

        assert os.path.isfile(
            file_path
        ), f"File at {file_path} does not exist."

        with open(file_path) as f:
            template = json.load(f)

        logger.debug(f"Bootstrap plotly template loaded from {file_path}")

        return template

    def set_formatting(self, fig: go.Figure) -> None:
        """
        If template is provided, set it, otherwise run all the formatting helpers on figure.
        """
        if self.template_name is None:
            self._set_title_size(fig)
            self._set_background(fig)
        else:
            fig.update_layout(template=self.template)

    def set_start_end_date(self) -> None:
        """
        Extract the start/end date from the data.
        """
        date_format = "%Y-%m-%d"
        self.start_date = min(self.df[COLUMN.TIMESTAMP]).strftime(date_format)
        self.end_date = max(self.df[COLUMN.TIMESTAMP]).strftime(date_format)

    @abstractmethod
    def _validate_data(self, df: pd.DataFrame) -> None:
        """
        Assert that the data conforms to the plot function.
        """
        pass

    @abstractmethod
    def plot(self) -> go.Figure:
        """
        Main plotting function.
        """
        pass


class HistogramPlotter(BasePlotter):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.set_start_end_date()

    def _validate_data(self, df: pd.DataFrame) -> None:
        for column in [COLUMN.MIN, COLUMN.MAX, COLUMN.TIMESTAMP]:
            assert (
                column in df.columns
            ), f"Column {column} missing from the data columns ({df.columns})."

    def plot(self, show_title: bool = False) -> go.Figure:
        """
        Create a histogram for the min/max values.
        """

        long_df = self._preprocess_data_for_histogram()

        fig = px.histogram(
            long_df,
            x="value",
            color="variable",
            marginal="box",
            color_discrete_map={
                "Min": self.colors[COLOR_ITEM.MIN],
                "Max": self.colors[COLOR_ITEM.MAX],
            },
            labels={
                "variable": "Measure",
            },
        )

        if show_title:
            title = (
                f"Noise Level Distribution - {self.start_date} to {self.end_date}",
            )
            fig.update_layout(title=dict(text=title))

        fig.update_traces(opacity=0.75)

        fig.update_layout(
            barmode="overlay",
            yaxis_title="Count",
            xaxis_title="Noise Level (dBA)",
        )

        # add mean vlines
        means = long_df.groupby("variable")["value"].mean()
        self._add_vlines(fig, means["Min"], means["Max"])

        self.set_formatting(fig)

        return fig

    def _preprocess_data_for_histogram(self) -> pd.DataFrame:
        """
        Pivot data into long format for histogram plotting.
        """
        long_df = pd.melt(self.df[[COLUMN.MIN, COLUMN.MAX]])
        long_df["variable"] = long_df["variable"].map(
            {COLUMN.MIN: "Min", COLUMN.MAX: "Max"}
        )

        return long_df

    @staticmethod
    def _add_vlines(fig: go.Figure, min: float, max: float) -> None:
        fig.add_vline(
            x=min,
            row=1,
            line_width=3,
            line_dash="dash",
            line_color="grey",
            annotation_text=f"Average Min = {round(min, 1)}",
            annotation_position="top left",
        )
        fig.add_vline(
            x=max,
            row=1,
            line_width=3,
            line_dash="dash",
            line_color="grey",
            annotation_text=f"Average Max = {round(max, 1)}",
            annotation_position="top right",
        )


class TimeseriesPlotter(BasePlotter):
    """
    Plotting the noise data over time.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.noise_threshold = int(
            self._config["constants"]["noise_threshold"]
        )

        self.set_start_end_date()
        self.outliers = filter_outliers(
            self.df, threshold=self.noise_threshold
        )

    def _validate_data(self, df: pd.DataFrame) -> None:
        for column in [COLUMN.MIN, COLUMN.MAX, COLUMN.TIMESTAMP]:
            assert (
                column in df.columns
            ), f"Column {column} missing from the data columns ({df.columns})."

        assert ptype.is_datetime64_any_dtype(
            df[COLUMN.TIMESTAMP]
        ), f"Timestamp should be datatime data type, not {df[COLUMN.TIMESTAMP].dtype}."

    def plot(self, show_title: bool = False) -> go.Figure:
        """
        Create line chart showing the noise level over time.
        """
        figure = go.Figure()

        figure.add_traces(
            [
                self._get_min_line_trace(),
                self._get_max_line_trace(),
                self._get_outlier_trace(),
                self._get_indicator_trace(),
            ]
        )

        figure.update_xaxes(rangeslider_visible=True)
        figure.update_yaxes(title_text="Noise Level (dBA)")
        figure.update_layout(
            showlegend=False,
            hovermode="x unified",
        )

        if show_title:
            title = f"Noise Level - {self.start_date} to {self.end_date}"
            figure.update_layout(title=dict(text=title))

        self.set_formatting(figure)

        return figure

    def _get_outlier_trace(self) -> go.Scatter:
        trace = go.Scatter(
            x=self.outliers[COLUMN.TIMESTAMP],
            y=self.outliers[COLUMN.MAX],
            name="outlier",
            mode="markers",
            marker=dict(
                size=int(self._config["plot.sizes"]["marker"]),
                color=self.colors[COLOR_ITEM.MAX],
            ),
        )

        return trace

    def _get_max_line_trace(self) -> go.Scatter:
        trace = go.Scatter(
            x=self.df[COLUMN.TIMESTAMP],
            y=self.df[COLUMN.MAX],
            name="max",
            mode="lines",
            line_color=self.colors[COLOR_ITEM.MAX],
            fill="tonexty",
            fillcolor=self._config["plot.colors"]["fill"],
        )

        return trace

    def _get_min_line_trace(self) -> go.Scatter:
        trace = go.Scatter(
            x=self.df[COLUMN.TIMESTAMP],
            y=self.df[COLUMN.MIN],
            name="min",
            mode="lines",
            line_color=self.colors[COLOR_ITEM.MIN],
        )
        return trace

    def _get_indicator_trace(self) -> go.Indicator:
        """
        Outlier count indicator.
        """
        indicator = go.Indicator(
            mode="number",
            value=self.outliers.shape[0],
            number={"font_color": self.colors[COLOR_ITEM.MAX]},
            title={
                "text": "# of outliers",
                "font_color": self.colors[COLOR_ITEM.MAX],
            },
            domain={"x": [0.8, 1], "y": [0.8, 1]},
        )
        return indicator


class HeatmapPlotter(BasePlotter):
    """
    Create heatmap showing longterm trends in the data.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def _validate_data(self, df: pd.DataFrame) -> None:
        assert all(
            [
                col in df.columns
                for col in [
                    COLUMN.DATE,
                    COLUMN.HOUR,
                    COLUMN.MINNOISE,
                    COLUMN.MAXNOISE,
                ]
            ]
        )

    def _pivot(self, value: HEATMAP_VALUE) -> pd.DataFrame:
        """
        Transform from long to wide format with hours as indices, dates as columns.
        """
        pivot_table = pd.pivot_table(
            self.df, columns=[COLUMN.HOUR], index=[COLUMN.DATE], values=value
        )

        # add missing indices
        pivot_table = pivot_table.resample("D").asfreq()

        # transpose
        pivot_table = pivot_table.T

        # map names to string, otherwise plotly errors out
        pivot_table.index.name = COLUMN.HOUR.value
        pivot_table.columns.name = COLUMN.DATE.value

        return pivot_table

    def _get_colorscale_from_value(self, value: HEATMAP_VALUE) -> List[str]:
        """
        Construct the colorscale depending on the value to be shown.
        """
        low_color = self._config["plot.colors"]["heatmap_low"]

        if value == HEATMAP_VALUE.MIN:
            high_color = self.colors[COLOR_ITEM.MIN]
        elif value == HEATMAP_VALUE.MAX:
            high_color = self.colors[COLOR_ITEM.MAX]

        return [low_color, high_color]

    def plot(
        self,
        pivot_value: HEATMAP_VALUE,
        title: Optional[str] = None,
        show_title: bool = False,
    ) -> go.Figure:
        """
        Create a heatmap from the pivot table.
        """
        pivot_column = pivot_value.value
        pivot_table = self._pivot(value=pivot_column)

        fig = px.imshow(
            pivot_table,
            x=pivot_table.columns,
            y=pivot_table.index,
            color_continuous_scale=self._get_colorscale_from_value(
                pivot_value
            ),
            height=300,
        )

        if show_title:
            fig.update_layout(title=dict(text=title))

        fig.update_layout(
            margin=dict(t=10),
            xaxis_title="Click a column to filter the line chart!",
            yaxis_title="Hour of Day",
            coloraxis_colorbar=dict(
                title="dBA",
            ),
        )
        self.set_formatting(fig)

        return fig


class AbstractIndicatorPlotter(BasePlotter):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    @staticmethod
    def _get_indicator(
        value: float, text: str, **indicator_kwargs
    ) -> go.Figure:
        fig = go.Figure(
            go.Indicator(
                mode="number+delta",
                value=value,
                title={"text": text},
                domain={"x": [0, 1], "y": [0, 1]},
                **indicator_kwargs,
            )
        )

        return fig


class DeviceCountIndicatorPlotter(AbstractIndicatorPlotter):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def _validate_data(self, df: pd.DataFrame) -> None:
        for col in [COLUMN.DEVICEID, COLUMN.COUNT, COLUMN.COUNT_PRIOR]:
            assert col in df.columns
        assert df[COLUMN.DEVICEID].nunique() == df.shape[0]

    def _get_device_count(self) -> int:
        """Current device count."""
        return (self.df[COLUMN.COUNT] > 0).sum()

    def _get_reference_count(self) -> int:
        """Current device count."""
        return (self.df[COLUMN.COUNT_PRIOR] > 0).sum()

    def plot(self) -> go.Figure:
        fig = self._get_indicator(
            value=self._get_device_count(),
            text="Number of Active Devices",
            delta={
                "reference": self._get_reference_count(),
                "relative": False,
                "increasing.color": "green",
                "decreasing.color": "red",
            },
        )

        self.set_formatting(fig)

        return fig


class MinAverageIndicatorPlotter(AbstractIndicatorPlotter):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def _validate_data(self, df: pd.DataFrame) -> None:
        for col in [
            COLUMN.DEVICEID,
            COLUMN.AVGMIN,
            COLUMN.COUNT,
            COLUMN.AVGMIN_PRIOR,
            COLUMN.COUNT_PRIOR,
        ]:
            assert col in df.columns

        assert df[COLUMN.DEVICEID].nunique() == df.shape[0]

    def _get_min_avg(self, count_col: COLUMN, min_col: COLUMN) -> float:
        """
        Find system avg: individual device-level avg multiplied by device count for device total (disaggregate first) then get global average.
        """
        total = sum(self.df[min_col] * self.df[count_col])
        avg = total / sum(self.df[count_col])

        return round(avg, 2)

    def _get_system_min_avg(self) -> float:
        """Current week."""
        return self._get_min_avg(COLUMN.COUNT, COLUMN.AVGMIN)

    def _get_reference_avg(self) -> float:
        """Prior week."""
        return self._get_min_avg(COLUMN.COUNT_PRIOR, COLUMN.AVGMIN_PRIOR)

    def plot(self) -> go.Figure:
        fig = self._get_indicator(
            value=self._get_system_min_avg(),
            text="Average Ambient Noise",
            delta={
                "reference": self._get_reference_avg(),
                "relative": True,
                "valueformat": ".1%",
                "increasing.color": "red",
                "decreasing.color": "green",
            },
            number={"suffix": " dBA"},
        )

        self.set_formatting(fig)

        return fig


class OutlierIndicatorPlotter(AbstractIndicatorPlotter):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    def _validate_data(self, df: pd.DataFrame) -> None:
        assert COLUMN.OUTLIERCOUNT in df.columns
        assert COLUMN.OUTLIERCOUNT_PRIOR in df.columns

    def _get_total_count(self) -> int:
        return self.df[COLUMN.OUTLIERCOUNT].sum()

    def _get_reference_count(self) -> int:
        return self.df[COLUMN.OUTLIERCOUNT_PRIOR].sum()

    def plot(self) -> go.Figure:
        fig = self._get_indicator(
            value=self._get_total_count(),
            text="Number of Outliers",
            delta={
                "reference": self._get_reference_count(),
                "relative": False,
                "increasing.color": "red",
                "decreasing.color": "green",
            },
        )

        self.set_formatting(fig)

        return fig
