"""
Module for the database cruncher which makes a linear interpolator from a subset of scenarios
"""

from pyam import IamDataFrame

from .base import _DatabaseCruncher
from ..utils import _get_unit_of_variable, _make_wide_db, make_interpolator


class DatabaseCruncherSSPSpecificRelation(_DatabaseCruncher):
    """
    Database cruncher which pre-filters to only use data from specific scenarios, then
    makes a linear interpolator to return values from that set of scenarios. Uses mean
    values in the case of repeated leader values. Returns the follower values at the
    extreme leader values for leader values more extreme than that found in the input
    data.

    """

    def derive_relationship(
        self, variable_follower, variable_leaders, required_scenario="*"
    ):
        """
        Derive the relationship between two variables from the database.

        Parameters
        ----------
        variable_follower : str
            The variable for which we want to calculate timeseries (e.g.
            ``"Emissions|CH4"``).

        variable_leaders : list[str]
            The variable(s) we want to use in order to infer timeseries of
            ``variable_follower`` (e.g. ``["Emissions|CO2"]``).

        required_scenario : str or list[str]
            The string which all accepted scenarios are required to match. This may have
            *s to represent wild cards. It defaults to accept all scenarios.

        Returns
        -------
        :obj:`func`
            Function which takes a :obj:`pyam.IamDataFrame` containing
            ``variable_leaders`` timeseries and returns timeseries for
            ``variable_follower`` based on the derived relationship between the two.
            Please see the source code for the exact definition (and docstring) of the
            returned function.

        Raises
        ------
        ValueError
            There is no data of the appropriate type in the database.
             There may be a typo in the SSP option.
        """
        if len(variable_leaders) != 1:
            raise NotImplementedError(
                "Having more than one `variable_leaders` is not yet implemented"
            )
        use_db = self._db.filter(
            scenario=required_scenario,
            variable=[variable_leaders[0], variable_follower],
        )
        if use_db.data.empty:
            raise ValueError(
                "There is no data of the appropriate type in the database."
                " There may be a typo in the SSP option."
            )
        leader_units = _get_unit_of_variable(use_db, variable_leaders)
        follower_units = _get_unit_of_variable(use_db, variable_follower)
        if len(leader_units) == 0:
            raise ValueError(
                "No data for `variable_leaders` ({}) in database".format(
                    variable_leaders
                )
            )
        if len(follower_units) == 0:
            raise ValueError(
                "No data for `variable_follower` ({}) in database".format(
                    variable_follower
                )
            )
        leader_units = leader_units[0]
        use_db_time_col = use_db.time_col
        use_db = _make_wide_db(use_db)
        interpolators = make_interpolator(
            variable_follower, variable_leaders, use_db, use_db_time_col
        )

        def filler(in_iamdf):
            """
            Filler function derived from :obj:`DatabaseCruncherSSPSpecificRelation`.

            Parameters
            ----------
            in_iamdf : :obj:`pyam.IamDataFrame`
                Input data to fill data in

            Returns
            -------
            :obj:`pyam.IamDataFrame`
                Filled in data (without original source data)

            Raises
            ------
            ValueError
                The key db_times for filling are not in ``in_iamdf``.
            """
            if use_db_time_col != in_iamdf.time_col:
                raise ValueError(
                    "`in_iamdf` time column must be the same as the time column used "
                    "to generate this filler function (`{}`)".format(use_db_time_col)
                )

            var_units = _get_unit_of_variable(in_iamdf, variable_leaders)
            if var_units.size == 0:
                raise ValueError(
                    "There is no data for {} so it cannot be infilled".format(
                        variable_leaders
                    )
                )
            var_units = var_units[0]
            lead_var = in_iamdf.filter(variable=variable_leaders)
            assert (
                lead_var["unit"].nunique() == 1
            ), "There are multiple units for the lead variable."
            if var_units != leader_units:
                raise ValueError(
                    "Units of lead variable is meant to be `{}`, found `{}`".format(
                        leader_units, var_units
                    )
                )
            times_needed = set(in_iamdf.data[in_iamdf.time_col])
            if any(x not in interpolators.keys() for x in times_needed):
                raise ValueError(
                    "Not all required timepoints are present in the database we "
                    "crunched, we crunched \n\t`{}`\nbut you passed in \n\t{}".format(
                        list(interpolators.keys()),
                        in_iamdf.timeseries().columns.tolist(),
                    )
                )
            output_ts = lead_var.timeseries()
            for time in times_needed:
                output_ts[time] = interpolators[time](output_ts[time])
            output_ts.reset_index(inplace=True)
            output_ts["variable"] = variable_follower
            output_ts["unit"] = follower_units[0]
            return IamDataFrame(output_ts)

        return filler
