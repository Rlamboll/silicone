import datetime as dt
import logging
import re

import numpy as np
import pandas as pd
import pytest
import scipy.interpolate
from base import _DataBaseCruncherTester
from pyam import IamDataFrame

import silicone.stats
from silicone.database_crunchers import QuantileRollingWindows

_ma = "model_a"
_mb = "model_b"
_mc = "model_c"
_sa = "scen_a"
_sb = "scen_b"
_sc = "scen_c"
_sd = "scen_d"
_se = "scen_e"
_eco2 = "Emissions|CO2"
_gtc = "Gt C/yr"
_ech4 = "Emissions|CH4"
_mtch4 = "Mt CH4/yr"
_ec5f12 = "Emissions|HFC|C5F12"
_ktc5f12 = "kt C5F12/yr"
_ec2f6 = "Emissions|HFC|C2F6"
_ktc2f6 = "kt C2F6/yr"
_msrvu = ["model", "scenario", "region", "variable", "unit"]


class TestDatabaseCruncherRollingWindows(_DataBaseCruncherTester):
    tclass = QuantileRollingWindows
    # The units in this dataframe are intentionally illogical for C5F12
    tdb = pd.DataFrame(
        [
            [_ma, _sa, "World", _eco2, _gtc, 1, 2, 3, 4],
            [_ma, _sb, "World", _eco2, _gtc, 1, 2, 2, 1],
            [_mb, _sa, "World", _eco2, _gtc, 0.5, 3.5, 3.5, 0.5],
            [_mb, _sb, "World", _eco2, _gtc, 3.5, 0.5, 0.5, 3.5],
            [_ma, _sa, "World", _ech4, _mtch4, 100, 200, 300, 400],
            [_ma, _sb, "World", _ech4, _mtch4, 100, 200, 250, 300],
            [_mb, _sa, "World", _ech4, _mtch4, 220, 260, 250, 230],
            [_mb, _sb, "World", _ech4, _mtch4, 50, 200, 500, 800],
            [_ma, _sa, "World", _ec5f12, _mtch4, 3.14, 4, 5, 6],
            [_ma, _sa, "World", _ec2f6, _ktc2f6, 1.2, 1.5, 1, 0.5],
        ],
        columns=_msrvu + [2010, 2030, 2050, 2070],
    )
    large_db = pd.DataFrame(
        [
            [_ma, _sa, "World", _eco2, _gtc, 1],
            [_ma, _sb, "World", _eco2, _gtc, 5],
            [_mb, _sc, "World", _eco2, _gtc, 0.5],
            [_mb, _sd, "World", _eco2, _gtc, 3.5],
            [_mb, _se, "World", _eco2, _gtc, 0.5],
            [_ma, _sa, "World", _ech4, _mtch4, 100],
            [_ma, _sb, "World", _ech4, _mtch4, 170],
            [_mb, _sc, "World", _ech4, _mtch4, 220],
            [_mb, _sd, "World", _ech4, _mtch4, 50],
            [_mb, _se, "World", _ech4, _mtch4, 150],
        ],
        columns=_msrvu + [2010],
    )

    small_db = pd.DataFrame(
        [[_mb, _sa, "World", _eco2, _gtc, 1.2], [_ma, _sb, "World", _eco2, _gtc, 2.3]],
        columns=_msrvu + [2010],
    )

    tdownscale_df = pd.DataFrame(
        [
            [_mc, _sa, "World", _eco2, _gtc, 1, 2, 3, 4],
            [_mc, _sb, "World", _eco2, _gtc, 0.5, 0.5, 0.5, 0.5],
            [_mc, _sc, "World", _eco2, _gtc, 5, 5, 5, 5],
            [_ma, _sc, "World", _eco2, _gtc, 1.5, 2.5, 2.8, 1.8],
        ],
        columns=_msrvu + [2010, 2030, 2050, 2070],
    )

    simple_df = pd.DataFrame(
        [
            [_mc, _sa, "World", _eco2, _gtc, 0, 1000, 5000],
            [_mc, _sb, "World", _eco2, _gtc, 1, 1000, 5000],
            [_mc, _sa, "World", _ech4, _mtch4, 0, 300, 500],
            [_mc, _sb, "World", _ech4, _mtch4, 1, 300, 500],
        ],
        columns=_msrvu + [2010, 2030, 2050],
    )

    def test_derive_relationship(self, test_db):
        tcruncher = self.tclass(test_db)
        res = tcruncher.derive_relationship("Emissions|CO2", ["Emissions|CH4"])
        assert callable(res)

    def test_derive_relationship_with_nans(self):
        tdb = self.tdb.copy()
        tdb.loc[(tdb["variable"] == _eco2) & (tdb["model"] == _ma), 2050] = np.nan
        tcruncher = self.tclass(IamDataFrame(tdb))
        res = tcruncher.derive_relationship("Emissions|CO2", ["Emissions|CH4"])
        # just make sure that this runs through and no error is raised
        assert callable(res)

    def test_derive_relationship_with_multicolumns(self):
        tdb = self.tdb.copy()
        tcruncher = self.tclass(IamDataFrame(tdb))
        error_msg = re.escape(
            "Having more than one `variable_leaders` is not yet implemented"
        )
        with pytest.raises(NotImplementedError, match=error_msg):
            tcruncher.derive_relationship(
                "Emissions|CO2", ["Emissions|CH4", "Emissions|HFC|C5F12"]
            )

    @pytest.mark.parametrize("use_ratio", [True, False])
    def test_relationship_usage(self, simple_df, use_ratio, caplog):
        # This tests that using the cruncher for a simple case (no averages, just
        # choosing one of two values) produces the expected results. We test the
        # quantiles that should result in a flip between the two states.
        tcruncher = self.tclass(simple_df)
        quant = 0.58
        res = tcruncher.derive_relationship(
            "Emissions|CO2",
            ["Emissions|CH4"],
            quantile=quant,
            nwindows=1,
            use_ratio=use_ratio,
        )
        with caplog.at_level(logging.INFO, logger="silicone.database_crunchers."):
            returned = res(simple_df)
        if use_ratio:
            # We have a 0/0*0 in the calculation, so no value appears.
            assert len(caplog.record_tuples) == 1
            assert returned.filter(scenario="scen_a", year=2010)["value"].iloc[0] == 0
        else:
            assert len(caplog.record_tuples) == 0
            assert np.isclose(
                returned.filter(scenario="scen_a", year=2010)["value"].iloc[0],
                (quant - 5 / 12) * 2,
            )
        # We are below the lower quantile limit at the bottom, as we have weighting of
        # 5/6 and 1/6 for 1 and 0 respectively

        # We are (quant - 1/12) along a gradient of 2
        assert np.isclose(
            returned.filter(scenario="scen_b", year=2010)["value"].iloc[0],
            (quant - 1 / 12) * 2,
        )
        assert all(returned.filter(year=2030)["value"] == 1000)
        assert all(returned.filter(year=2050)["value"] == 5000)

        # Now repeat with a higher quantile. This time we are too high in the second
        # case.
        quant = 0.59
        res = tcruncher.derive_relationship(
            "Emissions|CO2", ["Emissions|CH4"], quantile=quant, nwindows=1
        )
        result_2 = res(simple_df)
        assert np.isclose(
            result_2.filter(scenario="scen_a", year=2010)["value"].iloc[0],
            (quant - 5 / 12) * 2,
        )
        assert np.isclose(
            result_2.filter(scenario="scen_b", year=2010)["value"].iloc[0], 1,
        )
        assert all(result_2.filter(year=2030)["value"] == 1000)
        assert all(result_2.filter(year=2050)["value"] == 5000)

        # Similarly quantiles below 1/12 are 0 for the second case.
        res = tcruncher.derive_relationship(
            "Emissions|CO2", ["Emissions|CH4"], quantile=0.083, nwindows=1
        )
        with caplog.at_level(logging.INFO, logger="silicone.database_crunchers."):
            expect_00 = res(simple_df)
        if use_ratio:
            # We have 0/0*0, so no value appears.
            assert len(caplog.record_tuples) == 1
        else:
            assert len(caplog.record_tuples) == 0
        assert expect_00.filter(scenario="scen_a", year=2010)["value"].iloc[0] == 0
        assert expect_00.filter(scenario="scen_b", year=2010)["value"].iloc[0] == 0
        assert all(expect_00.filter(year=2030)["value"] == 1000)
        assert all(expect_00.filter(year=2050)["value"] == 5000)

    @pytest.mark.parametrize("use_ratio", [True, False])
    def test_numerical_relationship(self, use_ratio):
        # Calculate the values using the cruncher for a fairly detailed dataset
        large_db = IamDataFrame(self.large_db.copy())
        tcruncher = self.tclass(large_db)
        res = tcruncher.derive_relationship(
            "Emissions|CH4", ["Emissions|CO2"], use_ratio=use_ratio
        )
        assert callable(res)
        to_find = IamDataFrame(self.small_db.copy())
        crunched = res(to_find)

        # Calculate the same values numerically
        xs = large_db.filter(variable="Emissions|CO2")["value"].values
        ys = large_db.filter(variable="Emissions|CH4")["value"].values
        if use_ratio:
            ys = ys / xs
        quantile_expected = silicone.stats.rolling_window_find_quantiles(
            xs, ys, [0.5], nwindows=9
        )
        interpolate_fn = scipy.interpolate.interp1d(
            np.array(quantile_expected.index), quantile_expected.values.squeeze()
        )
        xs_to_interp = to_find.filter(variable="Emissions|CO2")["value"].values
        if use_ratio:
            expected = interpolate_fn(xs_to_interp) * xs_to_interp
        else:
            expected = interpolate_fn(xs_to_interp)
        assert all(crunched["value"].values == expected)

    @pytest.mark.parametrize("add_col", [None, "extra_col"])
    def test_extreme_values_relationship(self, add_col):
        # Our cruncher has a closest-point extrapolation algorithm and therefore
        # should return the same values when filling for data outside tht limits of
        # its cruncher

        # Calculate the values using the cruncher for a fairly detailed dataset
        large_db = IamDataFrame(self.large_db.copy())
        tcruncher = self.tclass(large_db)
        lead = ["Emissions|CO2"]
        follow = "Emissions|CH4"
        res = tcruncher.derive_relationship(follow, lead)
        assert callable(res)
        if add_col:
            add_col_val = "blah"
            large_db[add_col] = add_col_val
            large_db = IamDataFrame(large_db.data)
            assert large_db.extra_cols[0] == add_col
        crunched = res(large_db)

        # Increase the maximum values
        modify_extreme_db = large_db.filter(variable="Emissions|CO2").copy()
        ind = modify_extreme_db["value"].idxmax
        modify_extreme_db["value"].loc[ind] += 10
        extreme_crunched = res(modify_extreme_db)
        # Check results are the same
        assert all(crunched["value"] == extreme_crunched["value"])
        # Repeat with reducing the minimum value
        ind = modify_extreme_db["value"].idxmin
        modify_extreme_db["value"].loc[ind] -= 10
        extreme_crunched = res(modify_extreme_db)
        assert all(crunched["value"] == extreme_crunched["value"])

        # Check that we can append the answer
        appended_df = large_db.filter(variable=lead).append(crunched)
        assert appended_df.filter(variable=follow).equals(crunched)
        if add_col:
            assert all(appended_df[add_col] == add_col_val)

    def test_derive_relationship_same_gas(self, test_db, test_downscale_df):
        # Given only a single data series, we recreate the original pattern
        tcruncher = self.tclass(test_db)
        res = tcruncher.derive_relationship("Emissions|CO2", ["Emissions|CO2"])
        crunched = res(test_db)
        assert all(
            abs(
                crunched["value"].reset_index()
                - test_db.filter(variable="Emissions|CO2")["value"].reset_index()
            )
            < 1e15
        )

    def test_derive_relationship_error_no_info_leader(self, test_db):
        # test that crunching fails if there's no data about the lead gas in the
        # database
        variable_leaders = ["Emissions|CO2"]
        tcruncher = self.tclass(test_db.filter(variable=variable_leaders, keep=False))

        error_msg = re.escape(
            "No data for `variable_leaders` ({}) in database".format(variable_leaders)
        )
        with pytest.raises(ValueError, match=error_msg):
            tcruncher.derive_relationship("Emissions|CH4", variable_leaders)

    def test_crunch_error_no_info_leader(self, test_db):
        # test that crunching fails if there's no data about the lead gas in the
        # database
        variable_leaders = ["Emissions|CO2"]
        tcruncher = self.tclass(test_db)
        res = tcruncher.derive_relationship("Emissions|CH4", variable_leaders)
        error_msg = re.escape(
            "There is no data for {} so it cannot be infilled".format(variable_leaders)
        )
        with pytest.raises(ValueError, match=error_msg):
            res(test_db.filter(variable=variable_leaders, keep=False))

    def test_derive_relationship_error_no_info_follower(self, test_db):
        # test that crunching fails if there's no data about the follower gas in the
        # database
        variable_follower = "Emissions|CH4"
        tcruncher = self.tclass(test_db.filter(variable=variable_follower, keep=False))

        error_msg = re.escape(
            "No data for `variable_follower` ({}) in database".format(variable_follower)
        )
        with pytest.raises(ValueError, match=error_msg):
            tcruncher.derive_relationship(variable_follower, ["Emissions|CO2"])

    @pytest.mark.parametrize("quantile", (-0.1, 1.1, 10))
    def test_derive_relationship_error_quantile_out_of_bounds(self, test_db, quantile):
        tcruncher = self.tclass(test_db)
        error_msg = re.escape(
            "Invalid quantile ({}), it must be in [0, 1]".format(quantile)
        )

        with pytest.raises(ValueError, match=error_msg):
            tcruncher.derive_relationship(
                "Emissions|CH4", ["Emissions|CO2"], quantile=quantile
            )

    @pytest.mark.parametrize("nwindows", (1.1, 3.1, 101.2))
    def test_derive_relationship_nwindows_not_integer(self, test_db, nwindows):
        tcruncher = self.tclass(test_db)
        error_msg = re.escape(
            "Invalid nwindows ({}), it must be an integer".format(nwindows)
        )

        with pytest.raises(ValueError, match=error_msg):
            tcruncher.derive_relationship(
                "Emissions|CH4", ["Emissions|CO2"], nwindows=nwindows
            )

    @pytest.mark.parametrize("decay_length_factor", (0,))
    def test_derive_relationship_error_decay_length_factor_zero(
        self, test_db, decay_length_factor
    ):
        tcruncher = self.tclass(test_db)
        error_msg = re.escape("decay_length_factor must not be zero")

        with pytest.raises(ValueError, match=error_msg):
            tcruncher.derive_relationship(
                "Emissions|CH4",
                ["Emissions|CO2"],
                decay_length_factor=decay_length_factor,
            )

    def test_relationship_usage_wrong_unit(self, test_db, test_downscale_df):
        tcruncher = self.tclass(test_db)
        res = tcruncher.derive_relationship("Emissions|CO2", ["Emissions|CO2"])

        exp_units = test_db.filter(variable="Emissions|CO2")["unit"].iloc[0]

        wrong_unit = "t C/yr"
        test_downscale_df = self._adjust_time_style_to_match(test_downscale_df, test_db)
        test_downscale_df["unit"] = wrong_unit

        error_msg = re.escape(
            "Units of lead variable is meant to be `{}`, found `{}`".format(
                exp_units, wrong_unit
            )
        )
        with pytest.raises(ValueError, match=error_msg):
            res(test_downscale_df)

    def test_relationship_usage_wrong_time(self):
        tdb = IamDataFrame(self.tdb)
        tcruncher = self.tclass(tdb)
        res = tcruncher.derive_relationship("Emissions|CO2", ["Emissions|CO2"])

        test_downscale_df = IamDataFrame(self.tdb).timeseries()
        test_downscale_df.columns = test_downscale_df.columns.map(
            lambda x: dt.datetime(x, 1, 1)
        )
        test_downscale_df = IamDataFrame(test_downscale_df)

        error_msg = re.escape(
            "`in_iamdf` time column must be the same as the time column used "
            "to generate this filler function (`year`)"
        )
        with pytest.raises(ValueError, match=error_msg):
            res(test_downscale_df)

    def test_relationship_usage_insufficient_timepoints(
        self, test_db, test_downscale_df
    ):
        tcruncher = self.tclass(test_db.filter(year=2030, keep=False))

        filler = tcruncher.derive_relationship("Emissions|CH4", ["Emissions|CO2"])

        test_downscale_df = self._adjust_time_style_to_match(test_downscale_df, test_db)

        error_msg = re.escape(
            "Not all required timepoints are present in the database we "
            "crunched, we crunched \n\t`{}`\nbut you passed in \n\t{}".format(
                list(
                    test_db.filter(year=2030, keep=False).timeseries().columns.tolist()
                ),
                test_db.timeseries().columns.tolist(),
            )
        )
        with pytest.raises(ValueError, match=error_msg):
            filler(test_downscale_df)
