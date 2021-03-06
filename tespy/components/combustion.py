# -*- coding: utf-8

"""Module for components using combustion.

Components in this module:

    - :func:`tespy.components.combustion.combustion_chamber`
    - :func:`tespy.components.combustion.combustion_chamber_stoich`
    - :func:`tespy.components.combustion.combustion_engine`


This file is part of project TESPy (github.com/oemof/tespy). It's copyrighted
by the contributors recorded in the version control history of the file,
available from its original location tespy/components/combustion.py

SPDX-License-Identifier: MIT
"""

import CoolProp.CoolProp as CP

import logging

import numpy as np

from tespy.components.components import component

from tespy.tools.data_containers import (dc_cc, dc_cp, dc_simple)
from tespy.tools.fluid_properties import (
        h_mix_pT, h_pT, memorise, s_mix_ph, s_mix_pT, tespy_fluid, v_mix_ph)
from tespy.tools.global_vars import molar_masses
from tespy.tools.helpers import (
        fluid_structure, molar_mass_flow, TESPyComponentError)

# %%


class combustion_chamber(component):
    r"""
    The class combustion_chamber is parent class of all combustion components.

    Equations

        **mandatory equations**

        - :func:`tespy.components.combustion.combustion_chamber.reaction_balance`
        - :func:`tespy.components.components.component.mass_flow_func`

        .. math::

            0 = p_{in,i} - p_{out} \;
            \forall i \in \mathrm{inlets}

        - :func:`tespy.components.combustion.combustion_chamber.energy_balance`

        **optional equations**

        - :func:`tespy.components.combustion.combustion_chamber.lambda_func`
        - :func:`tespy.components.combustion.combustion_chamber.ti_func`

    Available fuels

        - methane, ethane, propane, butane, hydrogen

    Inlets/Outlets

        - in1, in2
        - out1

    Image

        .. image:: _images/combustion_chamber.svg
           :scale: 100 %
           :alt: alternative text
           :align: center

    .. note::

        The fuel and the air components can be connected to either of the
        inlets.

    Parameters
    ----------
    label : str
        The label of the component.

    design : list
        List containing design parameters (stated as String).

    offdesign : list
        List containing offdesign parameters (stated as String).

    lamb : float/tespy.helpers.dc_cp
        Actual oxygen to stoichiometric oxygen ratio, :math:`\lambda/1`.

    ti : float/tespy.helpers.dc_cp
        Thermal input, (:math:`{LHV \cdot \dot{m}_f}`),
        :math:`ti/\text{W}`.

    Note
    ----
    For more information on the usage of the combustion chamber see the
    examples section on github or look for the combustion chamber tutorials
    at tespy.readthedocs.io.

    Example
    -------
    The combustion chamber calculates energy input due to combustion as well as
    the flue gas composition based on the type of fuel and the amount of
    oxygen supplied. Using the parameters p_range and T_range is recommended
    when using combustion, as these stabilize the calculation. In this example
    a mixture of methane, hydrogen and carbondioxide is used as fuel.

    >>> from tespy.components import sink, source, combustion_chamber
    >>> from tespy.connections import connection
    >>> from tespy.networks import network
    >>> from tespy.tools.fluid_properties import T_bp_p
    >>> import shutil
    >>> fluid_list = ['Ar', 'N2', 'H2', 'O2', 'CO2', 'CH4', 'H2O']
    >>> nw = network(fluids=fluid_list, p_unit='bar', T_unit='C',
    ... p_range=[0.5, 10], T_range=[10, 1200], iterinfo=False)
    >>> amb = source('ambient air')
    >>> sf = source('fuel')
    >>> fg = sink('flue gas outlet')
    >>> comb = combustion_chamber('combustion chamber')
    >>> comb.component()
    'combustion chamber'
    >>> amb_comb = connection(amb, 'out1', comb, 'in1')
    >>> sf_comb = connection(sf, 'out1', comb, 'in2')
    >>> comb_fg = connection(comb, 'out1', fg, 'in1')
    >>> nw.add_conns(sf_comb, amb_comb, comb_fg)

    Specify the thermal input of the combustion chamber. At the given fluid
    compositions this determines the mass flow of the fuel. The outlet
    temperature of the flue gas determines the ratio of oxygen to fuel mass
    flow.

    >>> comb.set_attr(ti=500000)
    >>> amb_comb.set_attr(p=1, T=20, fluid={'Ar': 0.0129, 'N2': 0.7553,
    ... 'H2O': 0, 'CH4': 0, 'CO2': 0.0004, 'O2': 0.2314, 'H2': 0})
    >>> sf_comb.set_attr(T=25, fluid={'CO2': 0.03, 'H2': 0.01, 'Ar': 0,
    ... 'N2': 0, 'O2': 0, 'H2O': 0, 'CH4': 0.96})
    >>> comb_fg.set_attr(T=1200)
    >>> nw.solve('design')
    >>> round(comb.lamb.val, 3)
    2.017
    >>> comb.set_attr(lamb=2)
    >>> comb_fg.set_attr(T=np.nan)
    >>> nw.solve('design')
    >>> round(comb_fg.T.val, 1)
    1208.4
    """

    @staticmethod
    def component():
        return 'combustion chamber'

    @staticmethod
    def attr():
        return {'lamb': dc_cp(min_val=1),
                'ti': dc_cp(min_val=0),
                'S': dc_simple()}

    @staticmethod
    def inlets():
        return ['in1', 'in2']

    @staticmethod
    def outlets():
        return ['out1']

    def comp_init(self, nw):

        component.comp_init(self, nw)

        self.m_deriv = self.mass_flow_deriv()
        self.p_deriv = self.pressure_deriv()

        self.fuel_list = []
        fuels = ['methane', 'ethane', 'propane', 'butane', 'hydrogen']
        for f in fuels:
            self.fuel_list += [x for x in nw.fluids if x in [a.replace(' ', '')
                               for a in CP.get_aliases(f)]]

        if len(self.fuel_list) == 0:
            msg = ('Your network\'s fluids do not contain any fuels, that are '
                   'available for the component ' + self.label + ' of type ' +
                   self.component() + '. Available fuels are: ' + str(fuels) +
                   '.')
            logging.error(msg)
            raise TESPyComponentError(msg)

        else:
            msg = ('The fuels for component ' + self.label + ' of type ' +
                   self.component() + ' are: ' + str(self.fuel_list) + '.')
            logging.debug(msg)

        self.o2 = [x for x in nw.fluids if x in [a.replace(' ', '')
                   for a in CP.get_aliases('O2')]][0]
        self.co2 = [x for x in nw.fluids if x in [a.replace(' ', '')
                    for a in CP.get_aliases('CO2')]][0]
        self.h2o = [x for x in nw.fluids if x in [a.replace(' ', '')
                    for a in CP.get_aliases('H2O')]][0]
        self.n2 = [x for x in nw.fluids if x in [a.replace(' ', '')
                   for a in CP.get_aliases('N2')]][0]

        self.fuels = {}
        for f in self.fuel_list:
            self.fuels[f] = {}
            structure = fluid_structure(f)
            for el in ['C', 'H', 'O']:
                if el in structure.keys():
                    self.fuels[f][el] = structure[el]
                else:
                    self.fuels[f][el] = 0
            self.fuels[f]['LHV'] = self.calc_lhv(f)

    def calc_lhv(self, f):
        r"""
        calculates the lower heating value of the combustion chambers fuel.

        Parameters
        ----------
        f : str
            Alias of the fuel.

        Returns
        -------
        val : float
            Lower heating value of the combustion chambers fuel.

            .. math::
                LHV = -\frac{\sum_i {\Delta H_f^0}_i -
                \sum_j {\Delta H_f^0}_j }
                {M_{fuel}}\\
                \forall i \in \text{reation products},\\
                \forall j \in \text{reation educts},\\
                \Delta H_f^0: \text{molar formation enthalpy}
        """
        hf = {}
        hf['hydrogen'] = 0
        hf['methane'] = -74.85
        hf['ethane'] = -84.68
        hf['propane'] = -103.8
        hf['butane'] = -124.51
        hf[self.o2] = 0
        hf[self.co2] = -393.5
        # water (gaseous)
        hf[self.h2o] = -241.8

        key = set(list(hf.keys())).intersection(
                set([a.replace(' ', '')
                     for a in CP.get_aliases(f)]))

        val = (-(self.fuels[f]['H'] / 2 * hf[self.h2o] +
                 self.fuels[f]['C'] * hf[self.co2] -
                 ((self.fuels[f]['C'] + self.fuels[f]['H'] / 4) * hf[self.o2] +
                  hf[list(key)[0]])) /
               molar_masses[f] * 1000)

        return val

    def equations(self):
        r"""
        Calculates vector vec_res with results of equations for this component.

        Returns
        -------
        vec_res : list
            Vector of residual values.
        """
        vec_res = []

        ######################################################################
        # equations for fluids in reaction balance
        for fluid in self.inl[0].fluid.val.keys():
            vec_res += [self.reaction_balance(fluid)]

        ######################################################################
        # eqation for mass flow balance
        vec_res += self.mass_flow_func()

        ######################################################################
        # equations for pressure
        for i in self.inl:
            vec_res += [self.outl[0].p.val_SI - i.p.val_SI]

        ######################################################################
        # equation for energy balance
        vec_res += [self.energy_balance()]

        ######################################################################
        # equation for specified air to stoichiometric air ratio lamb
        if self.lamb.is_set:
            vec_res += [self.lambda_func()]

        ######################################################################
        # equation for speciified thermal input
        if self.ti.is_set:
            vec_res += [self.ti_func()]

        return vec_res

    def derivatives(self):
        r"""
        Calculates matrix of partial derivatives for given equations.

        Returns
        -------
        mat_deriv : ndarray
            Matrix of partial derivatives.
        """
        mat_deriv = []

        ######################################################################
        # derivatives for reaction balance
        j = 0
        deriv = np.zeros((self.num_fl, 3, self.num_fl + 3))
        for fluid in self.fluids:
            for i in range(3):
                deriv[j, i, 0] = self.rb_numeric_deriv('m', i, fluid)
                deriv[j, i, 3:] = self.rb_numeric_deriv('fluid', i, fluid)

            j += 1
        mat_deriv += deriv.tolist()

        ######################################################################
        # derivatives for mass balance equations
        mat_deriv += self.m_deriv

        ######################################################################
        # derivatives for pressure equations
        mat_deriv += self.p_deriv

        ######################################################################
        # derivatives for energy balance equations
        deriv = np.zeros((1, 3, self.num_fl + 3))
        for i in range(3):
            deriv[0, i, 0] = self.numeric_deriv(self.energy_balance, 'm', i)
            deriv[0, i, 1] = self.numeric_deriv(self.energy_balance, 'p', i)
            if i >= self.num_i:
                deriv[0, i, 2] = -(self.inl + self.outl)[i].m.val_SI
            else:
                deriv[0, i, 2] = (self.inl + self.outl)[i].m.val_SI
        mat_deriv += deriv.tolist()

        ######################################################################
        # derivatives for specified lamb
        if self.lamb.is_set:
            deriv = np.zeros((1, 3, self.num_fl + 3))
            deriv[0, 0, 0] = self.numeric_deriv(self.lambda_func, 'm', 0)
            deriv[0, 0, 3:] = self.numeric_deriv(self.lambda_func, 'fluid', 0)
            deriv[0, 1, 0] = self.numeric_deriv(self.lambda_func, 'm', 1)
            deriv[0, 1, 3:] = self.numeric_deriv(self.lambda_func, 'fluid', 1)
            mat_deriv += deriv.tolist()

        ######################################################################
        # derivatives for specified thermal input
        if self.ti.is_set:
            # stoichiometric combustion chamber
            if isinstance(self, combustion_chamber_stoich):
                pos = 3 + self.fluids.index('TESPy::' + self.fuel_alias.val)
                fuel = 'TESPy::' + self.fuel_alias.val

                deriv = np.zeros((1, 3, self.num_fl + 3))
                for i in range(2):
                    deriv[0, i, 0] = -self.inl[i].fluid.val[fuel]
                    deriv[0, i, pos] = -self.inl[i].m.val_SI
                deriv[0, 2, 0] = self.outl[0].fluid.val[fuel]
                deriv[0, 2, pos] = self.outl[0].m.val_SI
                mat_deriv += (deriv * self.lhv).tolist()
            # combustion chamber
            else:

                deriv = np.zeros((1, 3, self.num_fl + 3))
                for f in self.fuel_list:
                    pos = 3 + self.fluids.index(f)
                    lhv = self.fuels[f]['LHV']

                    for i in range(2):
                        deriv[0, i, 0] += -self.inl[i].fluid.val[f] * lhv
                        deriv[0, i, pos] = -self.inl[i].m.val_SI * lhv
                    deriv[0, 2, 0] += self.outl[0].fluid.val[f] * lhv
                    deriv[0, 2, pos] = self.outl[0].m.val_SI * lhv
                mat_deriv += deriv.tolist()

        return np.asarray(mat_deriv)

    def pressure_deriv(self):
        r"""
        Calculates the partial derivatives for all pressure equations.

        Returns
        -------
        deriv : list
            Matrix with partial derivatives for the fluid equations.
        """
        deriv = np.zeros((2, 3, self.num_fl + 3))
        for k in range(2):
            deriv[k][2][1] = 1
            deriv[k][k][1] = -1
        return deriv.tolist()

    def reaction_balance(self, fluid):
        r"""
        Calculates the reaction balance for one fluid.

        - determine molar mass flows of fuel and oxygen
        - calculate mole number of carbon and hydrogen atoms in fuel
        - calculate molar oxygen flow for stoichiometric combustion
        - calculate residual value of the fluids balance

        for excess fuel

        - calculate excess carbon and hydrogen in fuels
        - calculate excess fuel shares

        General equations

            .. math::

                \text{combustion chamber: } i \in [1,2], o \in [1]\\
                \text{combustion engine: } i \in [3,4], o \in [3]\\

                res = \sum_i \left(x_{fluid,i} \cdot \dot{m}_{i}\right) -
                \sum_j \left(x_{fluid,j} \cdot \dot{m}_{j}\right) \;
                \forall i, \; \forall j

                \dot{m}_{fluid,m} = \sum_i \frac{x_{fluid,i} \cdot \dot{m}_{i}}
                {M_{fluid}} \; \forall i

                \dot{m}_{O_2,m,stoich}=\frac{\dot{m}_{H_m}}{4} + \dot{m}_{C_m}

                \lambda = \frac{\dot{m}_{O_2,m}}{\dot{m}_{O_2,m,stoich}}

        Excess carbon and hydrogen

            .. math::

               \dot{m}_{H_{exc,m}} = \begin{cases}
               0 & \lambda \geq 1\\
               4 \cdot \left( \dot{m}_{O_2,m,stoich} -
               \dot{m}_{O_2,m}\right) & \lambda < 1
                \end{cases}

               \dot{m}_{C_{exc,m}} = \begin{cases}
               0 & \lambda \geq 1\\
               \dot{m}_{O_2,m,stoich} - \dot{m}_{O_2,m} & \lambda < 1
                \end{cases}

        Equation for fuel

            .. math::

                0 = res - \left(\dot{m}_{f,m} - \dot{m}_{f,exc,m}\right)
                \cdot M_{fuel}\\

                \dot{m}_{f,exc,m} = \begin{cases}
                0 & \lambda \geq 1\\
                \dot{m}_{f,m} - \frac{\dot{m}_{O_2,m}}
                {n_{C,fuel} + 0.25 \cdot n_{H,fuel}}
                \end{cases}

        Equation for oxygen

            .. math::

                0 = res - \begin{cases}
                -\frac{\dot{m}_{O_2,m} \cdot M_{O_2}}{\lambda} &
                \lambda \geq 1\\
                - \dot{m}_{O_2,m} \cdot M_{O_2} & \lambda < 1
                \end{cases}

        Equation for water

            .. math::

                0 = res + \left( \dot{m}_{H_m} - \dot{m}_{H_{exc,m}} \right)
                \cdot 0.5 \cdot M_{H_2O}

        Equation for carbondioxide

            .. math::

                0 = res + \left( \dot{m}_{C_m} - \dot{m}_{C_{exc,m}} \right)
                \cdot M_{CO_2}

        Equation for all other fluids

        .. math::

            0 = res

        Parameters
        ----------
        fluid : str
            The fluid to calculate the reation balance for.

        Returns
        -------
        res : float
            Residual value of equation.
        """

        if isinstance(self, combustion_engine):
            inl = self.inl[2:]
            outl = self.outl[2:]
        else:
            inl = self.inl
            outl = self.outl

        ######################################################################
        # molar mass flow for fuel and oxygen
        n_fuel = {}
        n_oxy_stoich = {}
        n_h = 0
        n_c = 0
        for f in self.fuel_list:
            n_fuel[f] = 0
            for i in inl:
                n = i.m.val_SI * i.fluid.val[f] / molar_masses[f]
                n_fuel[f] += n
                n_h += n * self.fuels[f]['H']
                n_c += n * self.fuels[f]['C']

            # stoichiometric oxygen requirement for each fuel
            n_oxy_stoich[f] = n_fuel[f] * (self.fuels[f]['H'] / 4 +
                                           self.fuels[f]['C'])

        n_oxygen = 0
        for i in inl:
            n_oxygen += (i.m.val_SI * i.fluid.val[self.o2] /
                         molar_masses[self.o2])

        ######################################################################
        # calculate stoichiometric oxygen
        n_oxygen_stoich = n_h / 4 + n_c

        ######################################################################
        # calculate lambda if not set
        if not self.lamb.is_set:
            self.lamb.val = n_oxygen / n_oxygen_stoich

        ######################################################################
        # calculate excess fuel if lambda is lower than 1
        if self.lamb.val < 1:
            n_h_exc = (n_oxygen_stoich - n_oxygen) * 4
            n_c_exc = (n_oxygen_stoich - n_oxygen)
        else:
            n_h_exc = 0
            n_c_exc = 0

        ######################################################################
        # equation for carbondioxide
        if fluid == self.co2:
            dm = (n_c - n_c_exc) * molar_masses[self.co2]

        ######################################################################
        # equation for water
        elif fluid == self.h2o:
            dm = (n_h - n_h_exc) / 2 * molar_masses[self.h2o]

        ######################################################################
        # equation for oxygen
        elif fluid == self.o2:
            if self.lamb.val < 1:
                dm = -n_oxygen * molar_masses[self.o2]
            else:
                dm = -n_oxygen / self.lamb.val * molar_masses[self.o2]

        ######################################################################
        # equation for fuel
        elif fluid in self.fuel_list:
            if self.lamb.val < 1:
                n_fuel_exc = (-(n_oxygen / n_oxygen_stoich - 1) *
                              n_oxy_stoich[fluid] *
                              (self.fuels[f]['H'] / 4 + self.fuels[f]['C']))
            else:
                n_fuel_exc = 0
            dm = -(n_fuel[fluid] - n_fuel_exc) * molar_masses[fluid]

        ######################################################################
        # equation for other fluids
        else:
            dm = 0

        res = dm
        for i in inl:
            res += i.fluid.val[fluid] * i.m.val_SI
        for o in outl:
            res -= o.fluid.val[fluid] * o.m.val_SI
        return res

    def rb_numeric_deriv(self, dx, pos, fluid):
        r"""
        Calculates derivative of the reaction balance to dx at components inlet
        or outlet in position pos for the fluid fluid.

        Parameters
        ----------
        dx : str
            Partial derivative.

        pos : int
            Position of connection regarding to inlets and outlet of the
            component, logic: ['in1', 'in2', ..., 'out1', ...] ->
            0, 1, ..., n, n + 1, ..., n + m

        fluid : str
            Fluid to calculate partial derivative of reaction balance for.

        Returns
        -------
        deriv : float/list
            Partial derivative(s) of the function :math:`f` to variable(s)
            :math:`x`.

            .. math::

                \frac{\partial f}{\partial x} = \frac{f(x + d) + f(x - d)}{2 d}
        """
        dm, dp, dh, df = 0, 0, 0, 0
        if dx == 'm':
            dm = 1e-4
        else:
            df = 1e-5

        if dx == 'fluid':
            deriv = []
            for f in self.inl[0].fluid.val.keys():
                val = (self.inl + self.outl)[pos].fluid.val[f]
                exp = 0
                if (self.inl + self.outl)[pos].fluid.val[f] + df <= 1:
                    (self.inl + self.outl)[pos].fluid.val[f] += df
                else:
                    (self.inl + self.outl)[pos].fluid.val[f] = 1
                exp += self.reaction_balance(fluid)
                if (self.inl + self.outl)[pos].fluid.val[f] - 2 * df >= 0:
                    (self.inl + self.outl)[pos].fluid.val[f] -= 2 * df
                else:
                    (self.inl + self.outl)[pos].fluid.val[f] = 0
                exp -= self.reaction_balance(fluid)
                (self.inl + self.outl)[pos].fluid.val[f] = val

                deriv += [exp / (2 * (dm + dp + dh + df))]

        else:
            exp = 0
            (self.inl + self.outl)[pos].m.val_SI += dm
            (self.inl + self.outl)[pos].p.val_SI += dp
            (self.inl + self.outl)[pos].h.val_SI += dh
            exp += self.reaction_balance(fluid)

            (self.inl + self.outl)[pos].m.val_SI -= 2 * dm
            (self.inl + self.outl)[pos].p.val_SI -= 2 * dp
            (self.inl + self.outl)[pos].h.val_SI -= 2 * dh
            exp -= self.reaction_balance(fluid)
            deriv = exp / (2 * (dm + dp + dh + df))

            (self.inl + self.outl)[pos].m.val_SI += dm
            (self.inl + self.outl)[pos].p.val_SI += dp
            (self.inl + self.outl)[pos].h.val_SI += dh

        return deriv

    def energy_balance(self):
        r"""
        Calculates the energy balance of the adiabatic combustion chamber.

        Returns
        -------
        res : float
            Residual value of equation.

            .. math::

                \begin{split}s
                res = & \sum_i \dot{m}_{in,i} \cdot
                \left( h_{in,i} - h_{in,i,ref} \right)\\
                & - \sum_j \dot{m}_{out,j} \cdot
                \left( h_{out,j} - h_{out,j,ref} \right)\\
                & + H_{I,f} \cdot \left(\sum_i \dot{m}_{in,i} \cdot x_{f,i} -
                \sum_j \dot{m}_{out,j} \cdot x_{f,j} \right)
                \end{split}\\

                \forall i \in \text{inlets}\; \forall j \in \text{outlets}\\
                x_{f}\text{: mass fraction of fuel}

        Note
        ----
        The temperature for the reference state is set to 20 °C, thus
        the water may be liquid. In order to make sure, the state is
        referring to the lower heating value, the necessary enthalpy
        difference for evaporation is added. The stoichiometric combustion
        chamber uses a different reference, you will find it in the
        :func:`tespy.components.combustion.combustion_chamber_stoich.energy_balance`
        documentation.

        - Reference temperature: 293.15 K.
        - Reference pressure: 1 bar.
        """
        T_ref = 293.15
        p_ref = 1e5

        res = 0
        for i in self.inl:
            res += i.m.val_SI * (i.h.val_SI -
                                 h_mix_pT([0, p_ref, 0, i.fluid.val], T_ref))

        for o in self.outl:
            dh = 0
            n_h2o = o.fluid.val[self.h2o] / molar_masses[self.h2o]
            if n_h2o > 0:
                p = p_ref * n_h2o / molar_mass_flow(o.fluid.val)
                h = h_pT(p, T_ref, self.h2o)
                try:
                    h_steam = CP.PropsSI('H', 'P', p, 'Q', 1, self.h2o)
                except ValueError:
                    h_steam = CP.PropsSI('H', 'P', 615, 'Q', 1, self.h2o)
                if h < h_steam:
                    dh = (h_steam - h) * o.fluid.val[self.h2o]

            res -= o.m.val_SI * (o.h.val_SI -
                                 h_mix_pT([0, p_ref, 0, o.fluid.val], T_ref) -
                                 dh)

        res += self.calc_ti()

        return res

    def lambda_func(self):
        r"""
        Calculates the residual for specified lambda.

        Returns
        -------
        val : float
            Residual value of function.

            .. math::

                \dot{m}_{fluid,m} = \sum_i \frac{x_{fluid,i} \cdot \dot{m}_{i}}
                {M_{fluid}}\\ \forall i \in inlets

                val = \frac{\dot{m}_{f,m}}{\dot{m}_{O_2,m} \cdot
                \left(n_{C,fuel} + 0.25 \cdot n_{H,fuel}\right)} - \lambda
        """
        if isinstance(self, combustion_engine):
            inl = self.inl[2:]
        else:
            inl = self.inl

        n_h = 0
        n_c = 0
        for f in self.fuel_list:
            n_fuel = 0
            for i in inl:
                n_fuel += i.m.val_SI * i.fluid.val[f] / molar_masses[f]
                n_h += n_fuel * self.fuels[f]['H']
                n_c += n_fuel * self.fuels[f]['C']

        n_oxygen = 0
        for i in inl:
            n_oxygen += (i.m.val_SI * i.fluid.val[self.o2] /
                         molar_masses[self.o2])

        n_oxygen_stoich = n_h / 4 + n_c

        return n_oxygen / n_oxygen_stoich - self.lamb.val

    def ti_func(self):
        r"""
        Calculates the residual for specified thermal input.

        Returns
        -------
        val : float
            Residual value of function.

            .. math::

                val = ti - \dot{m}_f \cdot LHV
        """
        return self.ti.val - self.calc_ti()

    def calc_ti(self):
        r"""
        Calculates the thermal input of the combustion chamber.

        Returns
        -------
        ti : float
            Thermal input.

            .. math::

                ti = LHV \cdot \left[\sum_i \left(\dot{m}_{in,i} \cdot x_{f,i}
                \right) - \dot{m}_{out,1} \cdot x_{f,1} \right]
                \; \forall i \in [1,2]
        """
        ti = 0
        for f in self.fuel_list:
            m = 0
            for i in self.inl:
                m += i.m.val_SI * i.fluid.val[f]

            for o in self.outl:
                m -= o.m.val_SI * o.fluid.val[f]

            ti += m * self.fuels[f]['LHV']

        return ti

    def bus_func(self, bus):
        r"""
        Calculates the residual value of the bus function.

        Parameters
        ----------
        bus : tespy.connections.bus
            TESPy bus object.

        Returns
        -------
        val : float
            Residual value of equation.

            .. math::

                val = LHV \cdot \dot{m}_{f} \cdot
                f_{char}\left( \frac{\dot{m}_{f}}{\dot{m}_{f,ref}}\right)
        """
        val = self.calc_ti()
        if np.isnan(bus.P_ref):
            expr = 1
        else:
            expr = abs(val / bus.P_ref)
        return val * bus.char.evaluate(expr)

    def bus_deriv(self, bus):
        r"""
        Calculates the matrix of partial derivatives of the bus function.

        Parameters
        ----------
        bus : tespy.connections.bus
            TESPy bus object.

        Returns
        -------
        mat_deriv : ndarray
            Matrix of partial derivatives.
        """
        deriv = np.zeros((1, 3, len(self.inl[0].fluid.val) + 3))
        for i in range(3):
            deriv[0, i, 0] = self.numeric_deriv(self.bus_func,
                                                'm', i, bus=bus)
            deriv[0, i, 3:] = self.numeric_deriv(self.bus_func,
                                                 'fluid', i, bus=bus)

        return deriv

    def initialise_fluids(self, nw):
        r"""
        Calculates reaction balance with given lambda of 3 for good generic
        starting values at the component's outlet.

        Parameters
        ----------
        nw : tespy.networks.network
            Network using this component object.
        """
        N_2 = 0.7655
        O_2 = 0.2345

        n_fuel = 1
        lamb = 3

        fact_fuel = {}
        sum_fuel = 0
        for f in self.fuel_list:
            fact_fuel[f] = 0
            for i in self.inl:
                fact_fuel[f] += i.fluid.val[f] / 2
            sum_fuel += fact_fuel[f]

        for f in self.fuel_list:
            fact_fuel[f] /= sum_fuel

        m_co2 = 0
        m_h2o = 0
        m_fuel = 0
        for f in self.fuel_list:
            m_co2 += (n_fuel * self.fuels[f]['C'] * molar_masses[self.co2] *
                      fact_fuel[f])
            m_h2o += (n_fuel * self.fuels[f]['H'] / 2 *
                      molar_masses[self.h2o] * fact_fuel[f])
            m_fuel += n_fuel * molar_masses[f] * fact_fuel[f]

        n_o2 = (m_co2 / molar_masses[self.co2] +
                0.5 * m_h2o / molar_masses[self.h2o]) * lamb

        m_air = n_o2 * molar_masses[self.o2] / O_2
        m_fg = m_air + m_fuel

        m_o2 = n_o2 * molar_masses[self.o2] * (1 - 1 / lamb)
        m_n2 = N_2 * m_air

        fg = {
            self.n2: m_n2 / m_fg,
            self.co2: m_co2 / m_fg,
            self.o2: m_o2 / m_fg,
            self.h2o: m_h2o / m_fg
        }

        for o in self.outl:
            for fluid, x in o.fluid.val.items():
                if not o.fluid.val_set[fluid] and fluid in fg.keys():
                    o.fluid.val[fluid] = fg[fluid]

    def convergence_check(self, nw):
        r"""
        Performs a convergence check.

        Parameters
        ----------
        nw : tespy.networks.network
            The network object using this component.

        Note
        ----
        Manipulate enthalpies/pressure at inlet and outlet if not specified
        by user to match physically feasible constraints, keep fluid
        composition within feasible range and then propagates it towards the
        outlet.
        """
        if isinstance(self, combustion_engine):
            inl = self.inl[2:]
            outl = self.outl[2:]
        else:
            inl = self.inl
            outl = self.outl

        m = 0
        for i in inl:
            if i.init_csv is False:
                if i.m.val_SI < 0 and not i.m.val_set:
                    i.m.val_SI = 0.01
                m += i.m.val_SI

        ######################################################################
        # check fluid composition
        for o in outl:
            if o.init_csv is False:
                fluids = [f for f in o.fluid.val.keys()
                          if not o.fluid.val_set[f]]
                for f in fluids:
                    if f not in [self.o2, self.co2, self.h2o] + self.fuel_list:
                        m_f = 0
                        for i in inl:
                            m_f += i.fluid.val[f] * i.m.val_SI

                        if abs(o.fluid.val[f] - m_f / m) > 0.03:
                            o.fluid.val[f] = m_f / m

                    elif f == self.o2:
                        if o.fluid.val[f] > 0.25:
                            o.fluid.val[f] = 0.2
                        if o.fluid.val[f] < 0.001:
                            o.fluid.val[f] = 0.05

                    elif f == self.co2:
                        if o.fluid.val[f] > 0.1:
                            o.fluid.val[f] = 0.075
                        if o.fluid.val[f] < 0.001:
                            o.fluid.val[f] = 0.02

                    elif f == self.h2o:
                        if o.fluid.val[f] > 0.1:
                            o.fluid.val[f] = 0.075
                        if o.fluid.val[f] < 0.001:
                            o.fluid.val[f] = 0.02

                    elif f in self.fuel_list:
                        if o.fluid.val[f] > 0:
                            o.fluid.val[f] = 0

        ######################################################################
        # flue gas propagation
        for o in outl:
            if o.init_csv is False:
                if o.m.val_SI < 0 and not o.m.val_set:
                    o.m.val_SI = 10
                nw.init_target(o, o.t)

                if o.h.val_SI < 7.5e5 and not o.h.val_set:
                    o.h.val_SI = 1e6

        ######################################################################
        # additional checks for performance improvement
        if self.lamb.val < 2 and not self.lamb.is_set:
            # search fuel and air inlet
            for i in inl:
                fuel_found = False
                if i.init_csv is False:
                    fuel = 0
                    for f in self.fuel_list:
                        fuel += i.fluid.val[f]
                    # found the fuel inlet
                    if fuel > 0.75 and not i.m.val_set:
                        fuel_found = True
                        fuel_inlet = i

                    # found the air inlet
                    if fuel < 0.75:
                        air_tmp = i.m.val_SI

            if fuel_found is True:
                fuel_inlet.m.val_SI = air_tmp / 25

    @staticmethod
    def initialise_source(c, key):
        r"""
        Returns a starting value for pressure and enthalpy at component's
        outlet.

        Parameters
        ----------
        c : tespy.connections.connection
            Connection to perform initialisation on.

        key : str
            Fluid property to retrieve.

        Returns
        -------
        val : float
            Starting value for pressure/enthalpy in SI units.

            .. math::

                val = \begin{cases}
                5 \cdot 10^5 & \text{key = 'p'}\\
                10^6 & \text{key = 'h'}
                \end{cases}
        """
        if key == 'p':
            return 5e5
        elif key == 'h':
            return 10e5

    @staticmethod
    def initialise_target(c, key):
        r"""
        Returns a starting value for pressure and enthalpy at component's
        inlet.

        Parameters
        ----------
        c : tespy.connections.connection
            Connection to perform initialisation on.

        key : str
            Fluid property to retrieve.

        Returns
        -------
        val : float
            Starting value for pressure/enthalpy in SI units.

            .. math::

                val = \begin{cases}
                5  \cdot 10^5 & \text{key = 'p'}\\
                5 \cdot 10^5 & \text{key = 'h'}
                \end{cases}
        """
        if key == 'p':
            return 5e5
        elif key == 'h':
            return 5e5

    def calc_parameters(self):
        r"""
        Postprocessing parameter calculation.
        """
        self.ti.val = self.calc_ti()

        n_h = 0
        n_c = 0
        for f in self.fuel_list:
            n_fuel = 0
            for i in self.inl:
                n_fuel += i.m.val_SI * i.fluid.val[f] / molar_masses[f]
                n_h += n_fuel * self.fuels[f]['H']
                n_c += n_fuel * self.fuels[f]['C']

        n_oxygen = 0
        for i in self.inl:
            n_oxygen += (i.m.val_SI * i.fluid.val[self.o2] /
                         molar_masses[self.o2])

        n_oxygen_stoich = n_h / 4 + n_c

        self.lamb.val = n_oxygen / n_oxygen_stoich

        self.check_parameter_bounds()

# %%


class combustion_chamber_stoich(combustion_chamber):
    r"""
    The class combustion_chamber_stoich is a simplified combustion chamber.

    Equations

        **mandatory equations**

        - :func:`tespy.components.combustion.combustion_chamber_stoich.reaction_balance`
        - :func:`tespy.components.components.component.mass_flow_func`

        .. math::

            0 = p_{in,i} - p_{out} \;
            \forall i \in \mathrm{inlets}

        - :func:`tespy.components.combustion.combustion_chamber_stoich.energy_balance`

        **optional equations**

        - :func:`tespy.components.combustion.combustion_chamber_stoich.lambda_func`
        - :func:`tespy.components.combustion.combustion_chamber_stoich.ti_func`

    Available fuels

        - methane, ethane, propane, butane, hydrogen

    Inlets/Outlets

        - in1, in2
        - out1

    Image

        .. image:: _images/combustion_chamber.svg
           :scale: 100 %
           :alt: alternative text
           :align: center

    .. note::

        The fuel and the air components can be connected to either of the
        inlets.

    Parameters
    ----------
    label : str
        The label of the component.

    design : list
        List containing design parameters (stated as String).

    offdesign : list
        List containing offdesign parameters (stated as String).

    fuel : dict
        Fuel composition, e. g. :code:`{'CH4': 0.96, 'CO2': 0.04}`.

    fuel_alias : str
        Alias for the fuel, name of fuel for usage in network will be
        TESPy::fuel_alias.

    air : dict
        Fresh air composition,
        e. g. :code:`{'N2': 0.76, 'O2': 0.23, 'Ar': 0.01}`.

    air_alias : str
        Alias for the fresh air, name of air for usage in network will be
        TESPy::air_alias.

    path : str
        Path to existing fluid property table.

    lamb : float/tespy.helpers.dc_cp
        Air to stoichiometric air ratio, :math:`\lambda/1`.

    ti : float/tespy.helpers.dc_cp
        Thermal input, (:math:`{LHV \cdot \dot{m}_f}`),
        :math:`ti/\text{W}`.

    Note
    ----
    This combustion chamber uses fresh air and its fuel as the only
    reactive gas components. Therefore note the following restrictions. You
    are to

    - specify the fluid composition of the fresh air,
    - fully define the fuel's fluid components,
    - provide the aliases of the fresh air and the fuel and
    - make sure, both of the aliases are part of the network fluid vector.

    If you choose 'Air' or 'air' as alias for the fresh air, TESPy will use
    the fluid properties from CoolProp's air. Else, a custom fluid
    'TESPy::yourairalias' will be created.

    The name of the flue gas will be: 'TESPy::yourfuelalias_fg'. It is also
    possible to use fluid mixtures for the fuel, e. g.
    :code:`fuel={CH4: 0.9, 'CO2': 0.1}`. If you specify a fluid mixture for
    the fuel, TESPy will automatically create a custom fluid called:
    'TESPy::yourfuelalias'. For more information see the examples section
    or look for the combustion chamber tutorials at tespy.readthedocs.io.

    Example
    -------
    The stoichiometric combustion chamber follows identical physical properties
    as the combustion chamber. The main difference is, that the fuel and the
    air are not stated component wise but are fixed mixtures.
    The main advantage of using the stoichimetric combustion chamber
    comes from a strong improvement in terms of calculation speed.
    This example will show the same calculation as presented in the combustion
    chamber example
    (see :func:`tespy.components.combustion.combustion_chamber`).

    >>> from tespy.components import sink, source, combustion_chamber_stoich
    >>> from tespy.connections import connection
    >>> from tespy.networks import network
    >>> from tespy.tools.fluid_properties import T_bp_p
    >>> import shutil
    >>> fluid_list = ['TESPy::myAir', 'TESPy::myFuel', 'TESPy::myFuel_fg']
    >>> nw = network(fluids=fluid_list, p_unit='bar', T_unit='C',
    ... p_range=[0.001, 10], T_range=[10, 2000], iterinfo=False)
    >>> amb = source('ambient air')
    >>> sf = source('fuel')
    >>> fg = sink('flue gas outlet')
    >>> comb = combustion_chamber_stoich('stoichiometric combustion chamber')
    >>> comb.component()
    'combustion chamber stoichiometric flue gas'
    >>> amb_comb = connection(amb, 'out1', comb, 'in1')
    >>> sf_comb = connection(sf, 'out1', comb, 'in2')
    >>> comb_fg = connection(comb, 'out1', fg, 'in1')
    >>> nw.add_conns(sf_comb, amb_comb, comb_fg)

    Specify the thermal input of the combustion chamber. At the given fluid
    compositions this determines the mass flow of the fuel. The outlet
    temperature of the flue gas determines the ratio of oxygen to fuel mass
    flow. The fluid composition of the fuel and the air are defined, too. The
    results show very small deviation from the actual combustion chamber.

    >>> comb.set_attr(fuel={'CH4': 0.96, 'CO2': 0.03, 'H2': 0.01},
    ... air={'Ar': 0.0129, 'N2': 0.7553, 'H2O': 0, 'CH4': 0, 'CO2': 0.0004,
    ... 'O2': 0.2314}, fuel_alias='myFuel', air_alias='myAir', ti=500000)
    >>> amb_comb.set_attr(T=20, p=1, fluid={'TESPy::myAir': 1,
    ... 'TESPy::myFuel': 0,'TESPy::myFuel_fg': 0})
    >>> sf_comb.set_attr(T=25, fluid={'TESPy::myAir': 0, 'TESPy::myFuel': 1,
    ... 'TESPy::myFuel_fg': 0})
    >>> comb_fg.set_attr(T=1200)
    >>> nw.solve('design')
    >>> round(comb.lamb.val, 3)
    2.01
    >>> comb.set_attr(lamb=2)
    >>> comb_fg.set_attr(T=np.nan)
    >>> nw.solve('design')
    >>> round(comb_fg.T.val, 1)
    1204.7
    >>> shutil.rmtree('./LUT', ignore_errors=True)
    """

    @staticmethod
    def component():
        return 'combustion chamber stoichiometric flue gas'

    @staticmethod
    def attr():
        return {'fuel': dc_simple(), 'fuel_alias': dc_simple(),
                'air': dc_simple(), 'air_alias': dc_simple(),
                'path': dc_simple(),
                'lamb': dc_cp(min_val=1),
                'ti': dc_cp(min_val=0),
                'S': dc_simple()}

    @staticmethod
    def inlets():
        return ['in1', 'in2']

    @staticmethod
    def outlets():
        return ['out1']

    @staticmethod
    def fuels():
        return ['methane', 'ethane', 'propane', 'butane',
                'hydrogen']

    def comp_init(self, nw):

        component.comp_init(self, nw)

        self.m_deriv = self.mass_flow_deriv()
        self.p_deriv = self.pressure_deriv()

        if not self.fuel.is_set or not isinstance(self.fuel.val, dict):
            msg = 'Must specify fuel composition for combustion chamber.'
            logging.error(msg)
            raise TESPyComponentError(msg)

        if not self.fuel_alias.is_set:
            msg = 'Must specify fuel alias for combustion chamber.'
            logging.error(msg)
            raise TESPyComponentError(msg)
        if 'TESPy::' in self.fuel_alias.val:
            msg = 'Can not use \'TESPy::\' at this point.'
            logging.error(msg)
            raise TESPyComponentError(msg)

        if not self.air.is_set or not isinstance(self.air.val, dict):
            msg = 'Must specify air composition for combustion chamber.'
            logging.error(msg)
            raise TESPyComponentError(msg)

        if not self.air_alias.is_set:
            msg = 'Must specify air alias for combustion chamber.'
            logging.error(msg)
            raise TESPyComponentError(msg)
        if 'TESPy::' in self.air_alias.val:
            msg = 'Can not use \'TESPy::\' at this point.'
            logging.error(msg)
            raise TESPyComponentError(msg)

        # adjust the names for required fluids according to naming in the
        # network air
        for f in self.air.val.keys():
            alias = [x for x in nw.fluids if x in [a.replace(' ', '')
                     for a in CP.get_aliases(f)]]
            if len(alias) > 0:
                self.air.val[alias[0]] = self.air.val.pop(f)

        # fuel
        for f in self.fuel.val.keys():
            alias = [x for x in self.air.val.keys() if x in [a.replace(' ', '')
                     for a in CP.get_aliases(f)]]
            if len(alias) > 0:
                self.fuel.val[alias[0]] = self.fuel.val.pop(f)

        # list of all fluids of air and fuel
        fluids = list(self.air.val.keys()) + list(self.fuel.val.keys())

        # oxygen
        alias = [x for x in fluids if x in [a.replace(' ', '')
                 for a in CP.get_aliases('O2')]]
        if len(alias) == 0:
            msg = 'Oxygen missing in input fluids.'
            logging.error(msg)
            raise TESPyComponentError(msg)
        else:
            self.o2 = alias[0]

        # carbondioxide
        self.co2 = [x for x in nw.fluids if x in [a.replace(' ', '')
                    for a in CP.get_aliases('CO2')]]
        if len(self.co2) == 0:
            self.co2 = 'CO2'
        else:
            self.co2 = self.co2[0]

        # water
        self.h2o = [x for x in nw.fluids if x in [a.replace(' ', '')
                    for a in CP.get_aliases('H2O')]]
        if len(self.h2o) == 0:
            self.h2o = 'H2O'
        else:
            self.h2o = self.h2o[0]

        for f in fluids:
            memorise.heos[f] = CP.AbstractState('HEOS', f)

        # calculate lower heating value of specified fuel
        self.lhv = self.calc_lhv()
        msg = ('Combustion chamber fuel (' + self.fuel_alias.val +
               ') LHV is ' + str(self.lhv) + ' for component ' +
               self.label + '.')
        logging.debug(msg)
        # generate fluid properties for stoichiometric flue gas
        self.stoich_flue_gas(nw)

    def calc_lhv(self):
        r"""
        Calculate the lower heating value of the combustion chambers fuel.

        Returns
        -------
        val : float
            Lower heating value of the combustion chambers fuel.

            .. math::

                LHV = \sum_{fuels} \left(-\frac{\sum_i {\Delta H_f^0}_i -
                \sum_j {\Delta H_f^0}_j }
                {M_{fuel}} \cdot x_{fuel} \right)\\
                \forall i \in \text{reation products},\\
                \forall j \in \text{reation educts},\\
                \forall fuel \in \text{fuels},\\
                \Delta H_f^0: \text{molar formation enthalpy},\\
                x_{fuel}: \text{mass fraction of fuel in fuel mixture}
        """
        hf = {}
        hf['hydrogen'] = 0
        hf['methane'] = -74.85
        hf['ethane'] = -84.68
        hf['propane'] = -103.8
        hf['butane'] = -124.51
        hf['O2'] = 0
        hf['CO2'] = -393.5
        # water (gaseous)
        hf['H2O'] = -241.8

        lhv = 0

        for f, x in self.fuel.val.items():
            molar_masses[f] = CP.PropsSI('M', f)
            fl = set(list(hf.keys())).intersection(
                    set([a.replace(' ', '') for a in CP.get_aliases(f)]))
            if len(fl) == 0:
                continue

            if list(fl)[0] in self.fuels():
                structure = fluid_structure(f)

                n = {}
                for el in ['C', 'H', 'O']:
                    if el in structure.keys():
                        n[el] = structure[el]
                    else:
                        n[el] = 0

                lhv += (-(n['H'] / 2 * hf['H2O'] + n['C'] * hf['CO2'] -
                          ((n['C'] + n['H'] / 4) * hf['O2'] +
                           hf[list(fl)[0]])) / molar_masses[f] * 1000) * x

        return lhv

    def stoich_flue_gas(self, nw):
        r"""
        Calculates the fluid composition of the stoichiometric flue gas and
        creates a custom fluid.

        - uses one mole of fuel as reference quantity and :math:`\lambda=1`
          for stoichiometric flue gas calculation (no oxygen in flue gas)
        - calculate molar quantities of (reactive) fuel components to determine
          water and carbondioxide mass fraction in flue gas
        - calculate required molar quantity for oxygen and required fresh
          air mass
        - calculate residual mass fractions for non reactive components of
          fresh air in the flue gas
        - calculate flue gas fluid composition
        - generate custom fluid porperties



        Reactive components in fuel

            .. math::

                m_{fuel} = \frac{1}{M_{fuel}}\\
                m_{CO_2} = \sum_{i} \frac{x_{i} \cdot m_{fuel} \cdot num_{C,i}
                \cdot M_{CO_{2}}}{M_{i}}\\
                m_{H_{2}O} = \sum_{i} \frac{x_{i} \cdot m_{fuel} \cdot
                num_{H,i} \cdot M_{H_{2}O}}{2 \cdot M_{i}}\\
                \forall i \in \text{fuels in fuel vector},\\
                num = \text{number of atoms in molecule}

        Other components of fuel vector

            .. math::

                m_{fg,j} = x_{j} \cdot m_{fuel}\\
                \forall j \in \text{non fuels in fuel vecotr, e. g. } CO_2,\\
                m_{fg,j} = \text{mass of fluid component j in flue gas}

        Non-reactive components in air

            .. math::

                n_{O_2} = \left( \frac{m_{CO_2}}{M_{CO_2}} +
                \frac{m_{H_{2}O}}
                {0,5 \cdot M_{H_{2}O}} \right) \cdot \lambda,\\
                n_{O_2} = \text{mol of oxygen required}\\
                m_{air} = \frac{n_{O_2} \cdot M_{O_2}}{x_{O_{2}, air}},\\
                m_{air} = \text{required total air mass}\\
                m_{fg,j} = x_{j, air} \cdot m_{air}\\
                m_{fg, O_2} = 0,\\
                m_{fg,j} = \text{mass of fluid component j in flue gas}

        Flue gas composition

            .. math::

                x_{fg,j} = \frac{m_{fg, j}}{m_{air} + m_{fuel}}

        Parameters
        ----------
        nw : tespy.networks.network
            TESPy network to generate stoichiometric flue gas for.
        """
        lamb = 1
        n_fuel = 1
        m_fuel = 1 / molar_mass_flow(self.fuel.val) * n_fuel
        m_fuel_fg = m_fuel
        m_co2 = 0
        m_h2o = 0
        molar_masses[self.h2o] = CP.PropsSI('M', self.h2o)
        molar_masses[self.co2] = CP.PropsSI('M', self.co2)
        molar_masses[self.o2] = CP.PropsSI('M', self.o2)

        self.fg = {}
        self.fg[self.co2] = 0
        self.fg[self.h2o] = 0

        for f, x in self.fuel.val.items():
            fl = set(list(self.fuels())).intersection(
                    set([a.replace(' ', '') for a in CP.get_aliases(f)]))

            if len(fl) == 0:
                if f in self.fg.keys():
                    self.fg[f] += x * m_fuel
                else:
                    self.fg[f] = x * m_fuel
            else:
                n_fluid = x * m_fuel / molar_masses[f]
                m_fuel_fg -= n_fluid * molar_masses[f]
                structure = fluid_structure(f)
                n = {}
                for el in ['C', 'H', 'O']:
                    if el in structure.keys():
                        n[el] = structure[el]
                    else:
                        n[el] = 0

                m_co2 += n_fluid * n['C'] * molar_masses[self.co2]
                m_h2o += n_fluid * n['H'] / 2 * molar_masses[self.h2o]

        self.fg[self.co2] += m_co2
        self.fg[self.h2o] += m_h2o

        n_o2 = (m_co2 / molar_masses[self.co2] +
                0.5 * m_h2o / molar_masses[self.h2o]) * lamb
        m_air = n_o2 * molar_masses[self.o2] / self.air.val[self.o2]

        self.air_min = m_air / m_fuel

        for f, x in self.air.val.items():
            if f != self.o2:
                if f in self.fg.keys():
                    self.fg[f] += m_air * x
                else:
                    self.fg[f] = m_air * x

        m_fg = m_fuel + m_air

        for f in self.fg.keys():
            self.fg[f] /= m_fg

        if not self.path.is_set:
            self.path.val = None
        tespy_fluid(self.fuel_alias.val, self.fuel.val,
                    [1000, nw.p_range_SI[1]], nw.T_range_SI,
                    path=self.path.val)
        tespy_fluid(self.fuel_alias.val + '_fg', self.fg,
                    [1000, nw.p_range_SI[1]], nw.T_range_SI,
                    path=self.path.val)
        msg = ('Generated lookup table for ' + self.fuel_alias.val +
               ' and for stoichiometric flue gas at stoichiometric '
               'combustion chamber ' + self.label + '.')
        logging.debug(msg)

        if self.air_alias.val not in ['Air', 'air']:
            tespy_fluid(self.air_alias.val, self.air.val,
                        [1000, nw.p_range_SI[1]], nw.T_range_SI,
                        path=self.path.val)
            msg = ('Generated lookup table for ' + self.air_alias.val +
                   ' at stoichiometric combustion chamber ' + self.label + '.')
        else:
            msg = ('Using CoolProp air at stoichiometric combustion chamber ' +
                   self.label + '.')
        logging.debug(msg)

    def reaction_balance(self, fluid):
        r"""
        Calculates the reaction balance for one fluid.

        - determine molar mass flows of fuel and oxygen
        - calculate excess fuel
        - calculate residual value of the fluids balance

        General equations

            .. math::

                res = \sum_i \left(x_{fluid,i} \cdot \dot{m}_{i}\right) -
                \sum_j \left(x_{fluid,j} \cdot \dot{m}_{j}\right)\\
                \forall i \in [1,2], \; \forall j \in [1]

                \dot{m}_{air,min} = \dot{m}_{fuel} \cdot air_{min}

                \lambda = \frac{\dot{m}_{air}}{\dot{m}_{air,min}}

        Equation for fuel

            .. math::

                0 = res - \left(\dot{m}_{f} - \dot{m}_{f,exc}\right)

                \dot{m}_{f,exc} = \begin{cases}
                0 & \lambda \geq 1\\
                \dot{m}_{f} - \frac{\dot{m}_{air}}
                {\lambda \cdot air_{min}} & \lambda < 1
                \end{cases}

        Equation for air

            .. math::

                0 = res - \begin{cases}
                -\dot{m}_{air,min} & \lambda \geq 1\\
                -\dot{m}_{air} & \lambda < 1
                \end{cases}

        Equation for stoichiometric flue gas

            .. math::

                0 = res + \dot{m}_{air,min} + \dot{m}_{f}

        Equation for all other fluids

        .. math::

            0 = res

        Parameters
        ----------
        fluid : str
            The fluid to calculate the reation balance for.

        Returns
        -------
        res : float
            Residual value of equation.
        """
        if self.air_alias.val in ['air', 'Air']:
            air = self.air_alias.val
        else:
            air = 'TESPy::' + self.air_alias.val
        fuel = 'TESPy::' + self.fuel_alias.val
        flue_gas = 'TESPy::' + self.fuel_alias.val + '_fg'

        ######################################################################
        # calculate fuel and air mass flow
        m_fuel = 0
        for i in self.inl:
            m_fuel += i.m.val_SI * i.fluid.val[fuel]

        m_air = 0
        for i in self.inl:
            m_air += i.m.val_SI * i.fluid.val[air]

        m_air_min = self.air_min * m_fuel

        ######################################################################
        # calculate lambda if not specified
        if not self.lamb.is_set:
            self.lamb.val = m_air / (self.air_min * m_fuel)

        ######################################################################
        # calculate excess fuel if lambda is smaller than 1
        m_fuel_exc = 0
        if self.lamb.val < 1:
            m_fuel_exc = m_fuel - m_air / (self.lamb.val * self.air_min)

        ######################################################################
        # equation for air
        if fluid == air:
            if self.lamb.val >= 1:
                dm = -m_air_min
            else:
                dm = -m_air

        ######################################################################
        # equation for fuel
        elif fluid == fuel:
            dm = -(m_fuel - m_fuel_exc)

        ######################################################################
        # equation for flue gas
        elif fluid == flue_gas:
            dm = m_air_min + m_fuel

        ######################################################################
        # equation for other components
        else:
            dm = 0

        res = dm
        for i in self.inl:
            res += i.fluid.val[fluid] * i.m.val_SI
        for o in self.outl:
            res -= o.fluid.val[fluid] * o.m.val_SI
        return res

    def energy_balance(self):
        r"""
        Calculates the energy balance of the adiabatic combustion chamber.

        Returns
        -------
        res : float
            Residual value of equation.

            .. math::
                res = \sum_i \dot{m}_{in,i} \cdot
                \left( h_{in,i} - h_{in,i,ref} \right) - \sum_j \dot{m}_{out,j}
                \cdot \left( h_{out,j} - h_{out,j,ref} \right) +
                H_{I,f} \cdot \left(\sum_i \dot{m}_{in,i} \cdot x_{f,i} -
                \sum_j \dot{m}_{out,j} \cdot x_{f,j} \right)
                \; \forall i \in \text{inlets}\; \forall j \in \text{outlets}

        Note
        ----
        The temperature for the reference state is set to 100 °C, as the
        custom fluid properties are inacurate at the dew-point of water in
        the flue gas!

        - Reference temperature: 373.15 K.
        - Reference pressure: 1 bar.
        """
        T_ref = 373.15
        p_ref = 1e5

        res = 0
        for i in self.inl:
            res += i.m.val_SI * (i.h.val_SI -
                                 h_mix_pT([0, p_ref, 0, i.fluid.val], T_ref))
        for o in self.outl:
            res -= o.m.val_SI * (o.h.val_SI -
                                 h_mix_pT([0, p_ref, 0, o.fluid.val], T_ref))

        return res + self.calc_ti()

    def lambda_func(self):
        r"""
        Calculates the residual for specified lambda.

        Returns
        -------
        val : float
            Residual value of function.

            .. math::

                val = \lambda - \frac{\dot{m}_{air}}{\dot{m}_{air,min}}
        """
        if self.air_alias.val in ['air', 'Air']:
            air = self.air_alias.val
        else:
            air = 'TESPy::' + self.air_alias.val
        fuel = 'TESPy::' + self.fuel_alias.val

        m_air = 0
        m_fuel = 0

        for i in self.inl:
            m_air += (i.m.val_SI * i.fluid.val[air])
            m_fuel += (i.m.val_SI * i.fluid.val[fuel])

        return self.lamb.val - m_air / (m_fuel * self.air_min)

    def ti_func(self):
        r"""
        Calculates the residual for specified thermal input.

        Returns
        -------
        val : float
            Residual value of function.

            .. math::

                val = ti - \dot{m}_f \cdot LHV
        """
        return self.ti.val - self.calc_ti()

    def calc_ti(self):
        r"""
        Calculates the thermal input of the combustion chamber.

        Returns
        -------
        ti : float
            Thermal input.

            .. math::

                ti = LHV \cdot \left[\sum_i \left(\dot{m}_{in,i} \cdot x_{f,i}
                \right) - \dot{m}_{out,1} \cdot x_{f,1} \right]
                \; \forall i \in [1,2]
        """
        fuel = 'TESPy::' + self.fuel_alias.val

        m = 0
        for i in self.inl:
            m += i.m.val_SI * i.fluid.val[fuel]

        for o in self.outl:
            m -= o.m.val_SI * o.fluid.val[fuel]

        return m * self.lhv

    def initialise_fluids(self, nw):
        r"""
        Calculates reaction balance with given lambda of 3 for good generic
        starting values at the component's outlet.

        Parameters
        ----------
        nw : tespy.networks.network
            Network using this component object.
        """
        if self.air_alias.val in ['air', 'Air']:
            air = self.air_alias.val
        else:
            air = 'TESPy::' + self.air_alias.val
        flue_gas = 'TESPy::' + self.fuel_alias.val + "_fg"

        for c in nw.comps.loc[self].o:
            if not c.fluid.val_set[air]:
                c.fluid.val[air] = 0.8
            if not c.fluid.val_set[flue_gas]:
                c.fluid.val[flue_gas] = 0.2

    def convergence_check(self, nw):
        r"""
        Performs a convergence check.

        Parameters
        ----------
        nw : tespy.networks.network
            The network object using this component.

        Note
        ----
        Manipulate enthalpies/pressure at inlet and outlet if not specified by
        user to match physically feasible constraints, keep fluid composition
        within feasible range and then propagates it towards the outlet.
        """
        if self.air_alias.val in ['air', 'Air']:
            air = self.air_alias.val
        else:
            air = 'TESPy::' + self.air_alias.val
        flue_gas = 'TESPy::' + self.fuel_alias.val + "_fg"
        fuel = 'TESPy::' + self.fuel_alias.val

        for c in nw.comps.loc[self].o:
            if not c.fluid.val_set[air]:
                if c.fluid.val[air] > 0.95:
                    c.fluid.val[air] = 0.95
                if c.fluid.val[air] < 0.5:
                    c.fluid.val[air] = 0.5

            if not c.fluid.val_set[flue_gas]:
                if c.fluid.val[flue_gas] > 0.5:
                    c.fluid.val[flue_gas] = 0.5
                if c.fluid.val[flue_gas] < 0.05:
                    c.fluid.val[flue_gas] = 0.05

            if not c.fluid.val_set[fuel]:
                if c.fluid.val[fuel] > 0:
                    c.fluid.val[fuel] = 0

            nw.init_target(c, c.t)

        for i in nw.comps.loc[self].i:
            if i.m.val_SI < 0 and not i.m.val_set:
                i.m.val_SI = 0.01

        for c in nw.comps.loc[self].o:
            if c.m.val_SI < 0 and not c.m.val_set:
                c.m.val_SI = 10
            nw.init_target(c, c.t)

        if self.lamb.val < 1 and not self.lamb.is_set:
            self.lamb.val = 2

    def calc_parameters(self):
        r"""
        Post and preprocessing parameter calculation/specification.
        """
        if self.air_alias.val in ['air', 'Air']:
            air = self.air_alias.val
        else:
            air = 'TESPy::' + self.air_alias.val
        fuel = 'TESPy::' + self.fuel_alias.val

        m_fuel = 0
        for i in self.inl:
            m_fuel += i.m.val_SI * i.fluid.val[fuel]

        m_air = 0
        for i in self.inl:
            m_air += i.m.val_SI * i.fluid.val[air]

        self.lamb.val = (m_air / m_fuel) / self.air_min

        S = 0
        T_ref = 373.15
        p_ref = 1e5

        for i in self.inl:
            S += i.m.val_SI * (s_mix_ph(i.to_flow()) -
                               s_mix_pT([0, p_ref, 0, i.fluid.val], T_ref))

        for o in self.outl:
            S -= o.m.val_SI * (s_mix_ph(o.to_flow()) -
                               s_mix_pT([0, p_ref, 0, o.fluid.val], T_ref))

        self.S.val = S

        ti = 0
        for i in self.inl:
            ti += i.m.val_SI * i.fluid.val[fuel] * self.lhv

        self.ti.val = ti

        self.check_parameter_bounds()

# %%


class combustion_engine(combustion_chamber):
    r"""
    An internal combustion engine supplies power and heat cogeneration.

    The combustion engine produces power and heat in cogeneration from fuel
    combustion. The combustion properties are identical to the combustion
    chamber. Thermal input and power output, heat output and heat losses are
    linked with an individual characteristic line for each property.

    Equations

        **mandatory equations**

        - :func:`tespy.components.combustion.combustion_engine.reaction_balance`
        - :func:`tespy.components.combustion.combustion_engine.fluid_func`
          (for cooling water)
        - :func:`tespy.components.combustion.combustion_engine.mass_flow_func`

        .. math::

            0 = p_{3,in} - p_{3,out}\\
            0 = p_{4,in} - p_{3,out}

        - :func:`tespy.components.combustion.combustion_engine.energy_balance`

        **optional equations**

        - :func:`tespy.components.combustion.combustion_engine.lambda_func`
        - :func:`tespy.components.combustion.combustion_engine.ti_func`
        - :func:`tespy.components.combustion.combustion_engine.Q1_func`
        - :func:`tespy.components.combustion.combustion_engine.Q2_func`

        .. math::

            0 = p_{1,in} \cdot pr1 - p_{1,out}\\
            0 = p_{2,in} \cdot pr2 - p_{2,out}

        - :func:`tespy.components.components.component.zeta_func`
        - :func:`tespy.components.components.component.zeta2_func`

    Available fuels

        - methane, ethane, propane, butane, hydrogen

    Inlets/Outlets

        - in1, in2 (cooling water), in3, in4 (air and fuel)
        - out1, out2 (cooling water), out3 (flue gas)

    Image

        .. image:: _images/combustion_engine.svg
           :scale: 100 %
           :alt: alternative text
           :align: center

    .. note::

        The fuel and the air components can be connected to either of the
        inlets.

    Parameters
    ----------
    label : str
        The label of the component.

    design : list
        List containing design parameters (stated as String).

    offdesign : list
        List containing offdesign parameters (stated as String).

    lamb : float/tespy.helpers.dc_cp
        Air to stoichiometric air ratio, :math:`\lambda/1`.

    ti : float/tespy.helpers.dc_cp
        Thermal input, (:math:`{LHV \cdot \dot{m}_f}`),
        :math:`ti/\text{W}`.

    P : str/float/tespy.helpers.dc_cp
        Power output, :math:`P/\text{W}`.

    Q1 : str/float/tespy.helpers.dc_cp
        Heat output 1, :math:`\dot Q/\text{W}`.

    Q2 : str/float/tespy.helpers.dc_cp
        Heat output 2, :math:`\dot Q/\text{W}`.

    Qloss : str/float/tespy.helpers.dc_cp
        Heat loss, :math:`\dot Q_{loss}/\text{W}`.

    pr1 : str/float/tespy.helpers.dc_cp
        Pressure ratio heat outlet 1, :math:`pr/1`.

    pr2 : str/float/tespy.helpers.dc_cp
        Pressure ratio heat outlet 2, :math:`pr/1`.

    zeta1 : str/float/tespy.helpers.dc_cp
        Pressure ratio heat outlet 2,
        :math:`\zeta/\frac{1}{\text{m}^4}`.

    zeta2 : str/float/tespy.helpers.dc_cp
        Pressure ratio heat outlet 2,
        :math:`\zeta/\frac{1}{\text{m}^4}`.

    tiP_char : str/tespy.helpers.dc_cc
        Characteristic line linking fuel input to power output.

    Q1_char : str/tespy.helpers.dc_cc
        Characteristic line linking heat output 1 to power output.

    Q2_char : str/tespy.helpers.dc_cc
        Characteristic line linking heat output 2 to power output.

    Qloss_char : str/tespy.helpers.dc_cc
        Characteristic line linking heat loss to power output.

    Note
    ----
    For more information on the usage of the combustion engine see the
    examples in the tespy_examples repository.

    Example
    -------
    The combustion chamber calculates energy input due to combustion as well as
    the flue gas composition based on the type of fuel and the amount of
    oxygen supplied. Using the parameters p_range and T_range is recommended
    when using combustion, as these stabilize the calculation. In this example
    a mixture of methane, hydrogen and carbondioxide is used as fuel. There are
    two cooling ports, the cooling water will flow through them in parallel.

    >>> from tespy.components import (sink, source, combustion_engine, merge,
    ... splitter)
    >>> from tespy.connections import connection, ref
    >>> from tespy.networks import network
    >>> import shutil
    >>> fluid_list = ['Ar', 'N2', 'O2', 'CO2', 'CH4', 'H2O']
    >>> nw = network(fluids=fluid_list, p_unit='bar', T_unit='C',
    ... p_range=[0.5, 10], T_range=[10, 1200], iterinfo=False)
    >>> amb = source('ambient')
    >>> sf = source('fuel')
    >>> fg = sink('flue gas outlet')
    >>> cw_in = source('cooling water inlet')
    >>> sp = splitter('cooling water splitter', num_out=2)
    >>> me = merge('cooling water merge', num_in=2)
    >>> cw_out = sink('cooling water outlet')
    >>> chp = combustion_engine(label='internal combustion engine')
    >>> chp.component()
    'combustion engine'
    >>> amb_comb = connection(amb, 'out1', chp, 'in3')
    >>> sf_comb = connection(sf, 'out1', chp, 'in4')
    >>> comb_fg = connection(chp, 'out3', fg, 'in1')
    >>> nw.add_conns(sf_comb, amb_comb, comb_fg)
    >>> cw_sp = connection(cw_in, 'out1', sp, 'in1')
    >>> sp_chp1 = connection(sp, 'out1', chp, 'in1')
    >>> sp_chp2 = connection(sp, 'out2', chp, 'in2')
    >>> chp1_me = connection(chp, 'out1', me, 'in1')
    >>> chp2_me = connection(chp, 'out2', me, 'in2')
    >>> me_cw = connection(me, 'out1', cw_out, 'in1')
    >>> nw.add_conns(cw_sp, sp_chp1, sp_chp2, chp1_me, chp2_me, me_cw)

    The combustion engine produces a power output of 10 MW the oxygen to
    stoichiometric oxygen ratio is set to 1. Only pressure ratio 1 is set as
    we reconnect both cooling water streams. At the merge all pressure values
    will be identical automatically. Reference the mass flow at the splitter
    to be split in half.

    >>> chp.set_attr(pr1=0.99, P=10e6, lamb=1.0,
    ... design=['pr1'], offdesign=['zeta1'])
    >>> amb_comb.set_attr(p=5, T=30, fluid={'Ar': 0.0129, 'N2': 0.7553,
    ... 'H2O': 0, 'CH4': 0, 'CO2': 0.0004, 'O2': 0.2314})
    >>> sf_comb.set_attr(m0=0.1, T=30, fluid={'CO2': 0, 'Ar': 0, 'N2': 0,
    ... 'O2': 0, 'H2O': 0, 'CH4': 1})
    >>> cw_sp.set_attr(p=3, T=60, m=50, fluid={'CO2': 0, 'Ar': 0, 'N2': 0,
    ... 'O2': 0, 'H2O': 1, 'CH4': 0})
    >>> sp_chp2.set_attr(m=ref(sp_chp1, 1, 0))
    >>> mode = 'design'
    >>> nw.solve(mode=mode)
    >>> nw.save('tmp')
    >>> round(chp.ti.val)
    22500000.0
    >>> round(chp.Q1.val)
    1743636.0
    >>> chp.set_attr(Q1=1.5e6, P=np.nan)
    >>> mode = 'offdesign'
    >>> nw.solve(mode=mode, init_path='tmp', design_path='tmp')
    >>> round(chp.ti.val)
    17427210.0
    >>> round(chp.P.val / chp.P.design, 3)
    0.747
    >>> shutil.rmtree('./tmp', ignore_errors=True)
    """

    @staticmethod
    def component():
        return 'combustion engine'

    @staticmethod
    def attr():
        return {'lamb': dc_cp(min_val=1),
                'ti': dc_cp(min_val=0),
                'P': dc_cp(val=1e6, d=1, min_val=1),
                'Q1': dc_cp(min_val=1), 'Q2': dc_cp(min_val=1),
                'Qloss': dc_cp(val=1e5, d=1, min_val=1),
                'pr1': dc_cp(max_val=1),
                'pr2': dc_cp(max_val=1),
                'zeta1': dc_cp(min_val=0),
                'zeta2': dc_cp(min_val=0),
                'tiP_char': dc_cc(method='TI'),
                'Q1_char': dc_cc(method='Q1'),
                'Q2_char': dc_cc(method='Q2'),
                'Qloss_char': dc_cc(method='QLOSS'),
                'S': dc_simple()}

    @staticmethod
    def inlets():
        return ['in1', 'in2', 'in3', 'in4']

    @staticmethod
    def outlets():
        return ['out1', 'out2', 'out3']

    def comp_init(self, nw):

        if not self.P.is_set:
            self.set_attr(P='var')
            msg = ('The power output of combustion engines must be set! '
                   'We are adding the power output of component ' +
                   self.label + ' as custom variable of the system.')
            logging.info(msg)

        if not self.Qloss.is_set:
            self.set_attr(Qloss='var')
            msg = ('The heat loss of combustion engines must be set! '
                   'We are adding the heat loss of component ' +
                   self.label + ' as custom variable of the system.')
            logging.info(msg)

        combustion_chamber.comp_init(self, nw)

        self.fl_deriv = self.fluid_deriv()
        self.m_deriv = self.mass_flow_deriv()
        self.p_deriv = self.pressure_deriv()

    def equations(self):
        r"""
        Calculates vector vec_res with results of equations for this component.

        Returns
        -------
        vec_res : list
            Vector of residual values.
        """
        vec_res = []

        ######################################################################
        # equations for fluids in combustion chamber
        for fluid in self.inl[0].fluid.val.keys():
            vec_res += [self.reaction_balance(fluid)]

        ######################################################################
        # equations for fluids in cooling loops
        vec_res += self.fluid_func()

        ######################################################################
        # equations for mass flow
        vec_res += self.mass_flow_func()

        ######################################################################
        # equations for pressure balance in combustion
        vec_res += [self.inl[2].p.val_SI - self.outl[2].p.val_SI]
        vec_res += [self.inl[2].p.val_SI - self.inl[3].p.val_SI]

        ######################################################################
        # equation for combustion engine energy balance
        vec_res += [self.energy_balance()]

        ######################################################################
        # equation for power to thermal input ratio from characteristic line
        vec_res += [self.tiP_char_func()]

        ######################################################################
        # equations for heat outputs from characteristic line
        vec_res += [self.Q1_char_func()]
        vec_res += [self.Q2_char_func()]

        ######################################################################
        # equation for heat loss from characteristic line
        vec_res += [self.Qloss_char_func()]

        ######################################################################
        # equation for specified lambda
        if self.lamb.is_set:
            vec_res += [self.lambda_func()]

        ######################################################################
        # equation for specified thermal input
        if self.ti.is_set:
            vec_res += [self.ti_func()]

        ######################################################################
        # equations for specified heat ouptputs
        if self.Q1.is_set:
            vec_res += [self.Q1_func()]

        if self.Q2.is_set:
            vec_res += [self.Q2_func()]

        ######################################################################
        # equations for specified pressure ratios at cooling loops
        if self.pr1.is_set:
            vec_res += [self.pr1.val * self.inl[0].p.val_SI -
                        self.outl[0].p.val_SI]

        if self.pr2.is_set:
            vec_res += [self.pr2.val * self.inl[1].p.val_SI -
                        self.outl[1].p.val_SI]

        ######################################################################
        # equations for specified zeta values at cooling loops
        if self.zeta1.is_set:
            vec_res += [self.zeta_func()]

        if self.zeta2.is_set:
            vec_res += [self.zeta2_func()]

        return vec_res

    def derivatives(self):
        r"""
        Calculates matrix of partial derivatives for given equations.

        Returns
        -------
        mat_deriv : ndarray
            Matrix of partial derivatives.
        """
        mat_deriv = []

        ######################################################################
        # derivatives for reaction balance
        deriv = np.zeros((self.num_fl, 7 + self.num_vars, self.num_fl + 3))
        j = 0
        for fluid in self.fluids:

            # fresh air and fuel inlets
            deriv[j, 2, 0] = self.rb_numeric_deriv('m', 2, fluid)
            deriv[j, 2, 3:] = self.rb_numeric_deriv('fluid', 2, fluid)
            deriv[j, 3, 0] = self.rb_numeric_deriv('m', 3, fluid)
            deriv[j, 3, 3:] = self.rb_numeric_deriv('fluid', 3, fluid)

            # combustion outlet
            deriv[j, 6, 0] = self.rb_numeric_deriv('m', 6, fluid)
            deriv[j, 6, 3:] = self.rb_numeric_deriv('fluid', 6, fluid)
            j += 1
        mat_deriv += deriv.tolist()

        ######################################################################
        # derivatives for cooling water fluid composition and mass flow
        mat_deriv += self.fl_deriv
        mat_deriv += self.m_deriv

        ######################################################################
        # derivatives for pressure equations
        mat_deriv += self.p_deriv

        ######################################################################
        # derivatives for energy balance
        eb_deriv = np.zeros((1, 7 + self.num_vars, self.num_fl + 3))

        # mass flow cooling water
        for i in [0, 1]:
            eb_deriv[0, i, 0] = -(self.outl[i].h.val_SI - self.inl[i].h.val_SI)

        # mass flow and pressure for combustion reaction
        for i in [2, 3, 6]:
            eb_deriv[0, i, 0] = self.numeric_deriv(self.energy_balance, 'm', i)
            eb_deriv[0, i, 1] = self.numeric_deriv(self.energy_balance, 'p', i)

        # enthalpy
        for i in range(4):
            eb_deriv[0, i, 2] = self.inl[i].m.val_SI
        for i in range(3):
            eb_deriv[0, i + 4, 2] = -self.outl[i].m.val_SI

        # fluid composition
        for f in self.fuel_list:
            pos = 3 + self.fluids.index(f)
            lhv = self.fuels[f]['LHV']
            eb_deriv[0, 2, pos] = self.inl[2].m.val_SI * lhv
            eb_deriv[0, 3, pos] = self.inl[3].m.val_SI * lhv
            eb_deriv[0, 6, pos] = -self.outl[2].m.val_SI * lhv

        # power and heat loss
        if self.P.is_var:
            eb_deriv[0, 7 + self.P.var_pos, 0] = (
                    self.numeric_deriv(self.energy_balance, 'P', 7))
        if self.Qloss.is_var:
            eb_deriv[0, 7 + self.Qloss.var_pos, 0] = (
                    self.numeric_deriv(self.energy_balance, 'Qloss', 7))
        mat_deriv += eb_deriv.tolist()

        ######################################################################
        # derivatives for thermal input to power charactersitics
        tiP_deriv = np.zeros((1, 7 + self.num_vars, self.num_fl + 3))
        for i in [2, 3, 6]:
            tiP_deriv[0, i, 0] = self.numeric_deriv(self.tiP_char_func, 'm', i)
            tiP_deriv[0, i, 3:] = (
                    self.numeric_deriv(self.tiP_char_func, 'fluid', i))

        if self.P.is_var:
            tiP_deriv[0, 7 + self.P.var_pos, 0] = (
                    self.numeric_deriv(self.tiP_char_func, 'P', 7))
        mat_deriv += tiP_deriv.tolist()

        ######################################################################
        # derivatives for heat output 1 to power charactersitics
        Q1_deriv = np.zeros((1, 7 + self.num_vars, self.num_fl + 3))
        Q1_deriv[0, 0, 0] = self.numeric_deriv(self.Q1_char_func, 'm', 0)
        Q1_deriv[0, 0, 2] = self.numeric_deriv(self.Q1_char_func, 'h', 0)
        Q1_deriv[0, 4, 2] = self.numeric_deriv(self.Q1_char_func, 'h', 4)
        for i in [2, 3, 6]:
            Q1_deriv[0, i, 0] = self.numeric_deriv(self.Q1_char_func, 'm', i)
            Q1_deriv[0, i, 3:] = (
                    self.numeric_deriv(self.Q1_char_func, 'fluid', i))

        if self.P.is_var:
            Q1_deriv[0, 7 + self.P.var_pos, 0] = (
                    self.numeric_deriv(self.Q1_char_func, 'P', 7))
        mat_deriv += Q1_deriv.tolist()

        ######################################################################
        # derivatives for heat output 2 to power charactersitics
        Q2_deriv = np.zeros((1, 7 + self.num_vars, self.num_fl + 3))
        Q2_deriv[0, 1, 0] = self.numeric_deriv(self.Q2_char_func, 'm', 1)
        Q2_deriv[0, 1, 2] = self.numeric_deriv(self.Q2_char_func, 'h', 1)
        Q2_deriv[0, 5, 2] = self.numeric_deriv(self.Q2_char_func, 'h', 5)
        for i in [2, 3, 6]:
            Q2_deriv[0, i, 0] = self.numeric_deriv(self.Q2_char_func, 'm', i)
            Q2_deriv[0, i, 3:] = (
                    self.numeric_deriv(self.Q2_char_func, 'fluid', i))

        if self.P.is_var:
            Q2_deriv[0, 7 + self.P.var_pos, 0] = (
                    self.numeric_deriv(self.Q2_char_func, 'P', 7))
        mat_deriv += Q2_deriv.tolist()

        ######################################################################
        # derivatives for heat loss to power charactersitics
        Ql_deriv = np.zeros((1, 7 + self.num_vars, self.num_fl + 3))
        for i in [2, 3, 6]:
            Ql_deriv[0, i, 0] = (
                    self.numeric_deriv(self.Qloss_char_func, 'm', i))
            Ql_deriv[0, i, 3:] = (
                    self.numeric_deriv(self.Qloss_char_func, 'fluid', i))

        if self.P.is_var:
            Ql_deriv[0, 7 + self.P.var_pos, 0] = (
                    self.numeric_deriv(self.Qloss_char_func, 'P', 7))
        if self.Qloss.is_var:
            Ql_deriv[0, 7 + self.Qloss.var_pos, 0] = (
                    self.numeric_deriv(self.Qloss_char_func, 'Qloss', 7))
        mat_deriv += Ql_deriv.tolist()

        ######################################################################
        # derivatives for specified lambda
        if self.lamb.is_set:
            lamb_deriv = np.zeros((1, 7 + self.num_vars, self.num_fl + 3))
            lamb_deriv[0, 2, 0] = self.numeric_deriv(self.lambda_func, 'm', 2)
            lamb_deriv[0, 2, 3:] = (
                    self.numeric_deriv(self.lambda_func, 'fluid', 2))
            lamb_deriv[0, 3, 0] = self.numeric_deriv(self.lambda_func, 'm', 3)
            lamb_deriv[0, 3, 3:] = (
                    self.numeric_deriv(self.lambda_func, 'fluid', 3))
            mat_deriv += lamb_deriv.tolist()

        ######################################################################
        # derivatives for specified thermal input
        if self.ti.is_set:
            ti_deriv = np.zeros((1, 7 + self.num_vars, self.num_fl + 3))
            for i in [2, 3, 6]:
                ti_deriv[0, i, 0] = self.numeric_deriv(self.ti_func, 'm', i)
                ti_deriv[0, i, 3:] = (
                        self.numeric_deriv(self.ti_func, 'fluid', i))
            mat_deriv += ti_deriv.tolist()

        ######################################################################
        # derivatives for specified heat outputs
        if self.Q1.is_set:
            Q_deriv = np.zeros((1, 7 + self.num_vars, self.num_fl + 3))
            Q_deriv[0, 0, 0] = - (self.outl[0].h.val_SI - self.inl[0].h.val_SI)
            Q_deriv[0, 0, 2] = self.inl[0].m.val_SI
            Q_deriv[0, 4, 2] = -self.inl[0].m.val_SI
            mat_deriv += Q_deriv.tolist()

        if self.Q2.is_set:
            Q_deriv = np.zeros((1, 7 + self.num_vars, self.num_fl + 3))
            Q_deriv[0, 1, 0] = - (self.outl[1].h.val_SI - self.inl[1].h.val_SI)
            Q_deriv[0, 1, 2] = self.inl[1].m.val_SI
            Q_deriv[0, 5, 2] = -self.inl[1].m.val_SI
            mat_deriv += Q_deriv.tolist()

        ######################################################################
        # derivatives for specified pressure ratio at cooling loops
        if self.pr1.is_set:
            pr1_deriv = np.zeros((1, 7 + self.num_vars, self.num_fl + 3))
            pr1_deriv[0, 0, 1] = self.pr1.val
            pr1_deriv[0, 4, 1] = -1
            mat_deriv += pr1_deriv.tolist()

        if self.pr2.is_set:
            pr2_deriv = np.zeros((1, 7 + self.num_vars, self.num_fl + 3))
            pr2_deriv[0, 1, 1] = self.pr2.val
            pr2_deriv[0, 5, 1] = -1
            mat_deriv += pr2_deriv.tolist()

        ######################################################################
        # derivatives for specified zeta values at cooling loops
        if self.zeta1.is_set:
            zeta1_deriv = np.zeros((1, 7 + self.num_vars, self.num_fl + 3))
            zeta1_deriv[0, 0, 0] = self.numeric_deriv(self.zeta_func, 'm', 0)
            zeta1_deriv[0, 0, 1] = self.numeric_deriv(self.zeta_func, 'p', 0)
            zeta1_deriv[0, 0, 2] = self.numeric_deriv(self.zeta_func, 'h', 0)
            zeta1_deriv[0, 4, 1] = self.numeric_deriv(self.zeta_func, 'p', 4)
            zeta1_deriv[0, 4, 2] = self.numeric_deriv(self.zeta_func, 'h', 4)
            mat_deriv += zeta1_deriv.tolist()

        if self.zeta2.is_set:
            zeta2_deriv = np.zeros((1, 7 + self.num_vars, self.num_fl + 3))
            zeta2_deriv[0, 1, 0] = self.numeric_deriv(self.zeta2_func, 'm', 1)
            zeta2_deriv[0, 1, 1] = self.numeric_deriv(self.zeta2_func, 'p', 1)
            zeta2_deriv[0, 1, 2] = self.numeric_deriv(self.zeta2_func, 'h', 1)
            zeta2_deriv[0, 5, 1] = self.numeric_deriv(self.zeta2_func, 'p', 5)
            zeta2_deriv[0, 5, 2] = self.numeric_deriv(self.zeta2_func, 'h', 5)
            mat_deriv += zeta2_deriv.tolist()

        return np.asarray(mat_deriv)

    def fluid_func(self):
        r"""
        Calculates the vector of residual values for cooling loop fluid balance
        equations.

        Returns
        -------
        vec_res : list
            Vector of residual values for component's fluid balance.

            .. math::

                0 = fluid_{i,in_{j}} - fluid_{i,out_{j}}\\
                \forall i \in \mathrm{fluid}, \; \forall j \in [1, 2]
        """
        vec_res = []

        for i in range(2):
            for fluid, x in self.inl[i].fluid.val.items():
                vec_res += [x - self.outl[i].fluid.val[fluid]]
        return vec_res

    def mass_flow_func(self):
        r"""
        Calculates the residual value for component's mass flow balance
        equation.

        Returns
        -------
        vec_res : list
            Vector with residual value for component's mass flow balance.

            .. math::

                0 = \dot{m}_{in,i} - \dot{m}_{out,i}\\
                \forall i \in [1, 2]\\
                0 = \dot{m}_{in,3} + \dot{m}_{in,4} - \dot{m}_{out,3}
        """

        vec_res = []
        for i in range(2):
            vec_res += [self.inl[i].m.val_SI - self.outl[i].m.val_SI]
        vec_res += [self.inl[2].m.val_SI + self.inl[3].m.val_SI -
                    self.outl[2].m.val_SI]
        return vec_res

    def fluid_deriv(self):
        r"""
        Calculates the partial derivatives for cooling loop fluid balance
        equations.

        Returns
        -------
        deriv : list
            Matrix with partial derivatives for the fluid equations.
        """
        deriv = np.zeros((self.num_fl * 2, 7 + self.num_vars, 3 + self.num_fl))
        for i in range(self.num_fl):
            deriv[i, 0, i + 3] = 1
            deriv[i, 4, i + 3] = -1
        for j in range(self.num_fl):
            deriv[i + 1 + j, 1, j + 3] = 1
            deriv[i + 1 + j, 5, j + 3] = -1
        return deriv.tolist()

    def mass_flow_deriv(self):
        r"""
        Calculates the partial derivatives for all mass flow balance equations.

        Returns
        -------
        deriv : list
            Matrix with partial derivatives for the fluid equations.
        """
        deriv = np.zeros((3, 7 + self.num_vars, self.num_fl + 3))
        for i in range(2):
            deriv[i, i, 0] = 1
        for j in range(2):
            deriv[j, self.num_i + j, 0] = -1
        deriv[2, 2, 0] = 1
        deriv[2, 3, 0] = 1
        deriv[2, 6, 0] = -1
        return deriv.tolist()

    def pressure_deriv(self):
        r"""
        Calculates the partial derivatives for combustion pressure equations.

        Returns
        -------
        deriv : list
            Matrix with partial derivatives for the fluid equations.
        """
        deriv = np.zeros((2, 7 + self.num_vars, self.num_fl + 3))
        for k in range(2):
            deriv[k, 2, 1] = 1
        deriv[0, 6, 1] = -1
        deriv[1, 3, 1] = -1
        return deriv.tolist()

    def energy_balance(self):
        r"""
        Calculates the energy balance of the combustion engine.

        Returns
        -------
        res : float
            Residual value of equation.

            .. math::

                \begin{split}
                0 = & \sum_i \dot{m}_{in,i} \cdot
                \left( h_{in,i} - h_{in,i,ref} \right)\\
                & - \sum_j \dot{m}_{out,3} \cdot
                \left( h_{out,3} - h_{out,3,ref} \right)\\
                & + H_{I,f} \cdot
                \left(\sum_i \left(\dot{m}_{in,i} \cdot x_{f,i} \right)-
                \dot{m}_{out,3} \cdot x_{f,3} \right)\\
                & - \dot{Q}_1 - \dot{Q}_2 - P - \dot{Q}_{loss}\\
                \end{split}\\
                \forall i \in [3,4]

        Note
        ----
        The temperature for the reference state is set to 20 °C, thus
        the water may be liquid. In order to make sure, the state is
        referring to the lower heating value, the necessary enthalpy
        difference for evaporation is added.

        - Reference temperature: 293.15 K.
        - Reference pressure: 1 bar.
        """
        T_ref = 293.15
        p_ref = 1e5

        res = 0
        for i in self.inl[2:]:
            res += i.m.val_SI * (i.h.val_SI -
                                 h_mix_pT([0, p_ref, 0, i.fluid.val], T_ref))

        for o in self.outl[2:]:
            dh = 0
            n_h2o = o.fluid.val[self.h2o] / molar_masses[self.h2o]
            if n_h2o > 0:
                p = p_ref * n_h2o / molar_mass_flow(o.fluid.val)
                h = h_pT(p, T_ref, self.h2o)
                h_steam = CP.PropsSI('H', 'P', p, 'Q', 1, self.h2o)
                if h < h_steam:
                    dh = (h_steam - h) * o.fluid.val[self.h2o]

            res -= o.m.val_SI * (o.h.val_SI -
                                 h_mix_pT([0, p_ref, 0, o.fluid.val], T_ref) -
                                 dh)

        res += self.calc_ti()

        # cooling water
        for i in range(2):
            res -= self.inl[i].m.val_SI * (self.outl[i].h.val_SI -
                                           self.inl[i].h.val_SI)

        # power output and heat loss
        res -= self.P.val + self.Qloss.val

        return res

    def bus_func(self, bus):
        r"""
        Calculates the value of the bus function.

        Parameters
        ----------
        bus : tespy.connections.bus
            TESPy bus object.

        Returns
        -------
        val : float
            Residual value of bus function.

            .. math::

                val = \begin{cases}
                LHV \cdot \dot{m}_{f} \cdot f_{char}\left( \frac{LHV \cdot
                \dot{m}_{f}}{LHV \cdot \dot{m}_{f, ref}}\right) &
                \text{key = 'TI'}\\
                P \cdot f_{char}\left( \frac{P}{P_{ref}}\right) &
                \text{key = 'P'}\\
                \left(\dot{Q}_1 + \dot{Q}_2\right) \cdot
                f_{char}\left( \frac{\dot{Q}_1 + \dot{Q}_2}{\dot{Q}_{1,ref} +
                \dot{Q}_{2,ref}}\right) & \text{key = 'Q'}\\
                \dot{Q}_1 \cdot f_{char}\left( \frac{\dot{Q}_1}
                {\dot{Q}_{1,ref}}\right) & \text{key = 'Q1'}\\
                \dot{Q}_2 \cdot f_{char}\left( \frac{\dot{Q}_2}
                {\dot{Q}_{2,ref}}\right) & \text{key = 'Q2'}\\
                \dot{Q}_{loss} \cdot f_{char}\left( \frac{\dot{Q}_{loss}}
                {\dot{Q}_{loss,ref}}\right) & \text{key = 'Qloss'}
                \end{cases}

                \dot{Q}_1=\dot{m}_1 \cdot \left( h_{1,out} - h_{1,in} \right)\\
                \dot{Q}_2=\dot{m}_2 \cdot \left( h_{2,out} - h_{2,in} \right)
        """

        ######################################################################
        # value for bus parameter of thermal input (TI)
        if bus.param == 'TI':
            ti = self.calc_ti()
            if np.isnan(bus.P_ref):
                expr = 1
            else:
                expr = abs(ti / bus.P_ref)
            return ti * bus.char.evaluate(expr)

        ######################################################################
        # value for bus parameter of power output (P)
        elif bus.param == 'P':
            P = self.calc_P()
            if np.isnan(bus.P_ref):
                expr = 1
            else:
                expr = abs(P / bus.P_ref)
            return P * bus.char.evaluate(expr)

        ######################################################################
        # value for bus parameter of total heat production (Q)
        elif bus.param == 'Q':
            val = 0
            for j in range(2):
                i = self.inl[j]
                o = self.outl[j]
                val += i.m.val_SI * (o.h.val_SI - i.h.val_SI)

            if np.isnan(bus.P_ref):
                expr = 1
            else:
                expr = abs(val / bus.P_ref)
            return val * bus.char.evaluate(expr)

        ######################################################################
        # value for bus parameter of heat production 1 (Q1)
        elif bus.param == 'Q1':
            i = self.inl[0]
            o = self.outl[0]
            val = i.m.val_SI * (o.h.val_SI - i.h.val_SI)

            if np.isnan(bus.P_ref):
                expr = 1
            else:
                expr = abs(val / bus.P_ref)
            return val * bus.char.evaluate(expr)

        ######################################################################
        # value for bus parameter of heat production 2 (Q2)
        elif bus.param == 'Q2':
            i = self.inl[1]
            o = self.outl[1]
            val = i.m.val_SI * (o.h.val_SI - i.h.val_SI)

            if np.isnan(bus.P_ref):
                expr = 1
            else:
                expr = abs(val / bus.P_ref)
            return val * bus.char.evaluate(expr)

        ######################################################################
        # value for bus parameter of heat loss (Qloss)
        elif bus.param == 'Qloss':
            Q = self.calc_Qloss()
            if np.isnan(bus.P_ref):
                expr = 1
            else:
                expr = abs(Q / bus.P_ref)
            return Q * bus.char.evaluate(expr)

        ######################################################################
        # missing/invalid bus parameter
        else:
            msg = ('The parameter ' + str(bus.param) +
                   ' is not a valid parameter for a ' + self.component() + '.')
            logging.error(msg)
            raise ValueError(msg)

    def bus_deriv(self, bus):
        r"""
        Calculates the matrix of partial derivatives of the bus function.

        Parameters
        ----------
        bus : tespy.connections.bus
            TESPy bus object.

        Returns
        -------
        mat_deriv : ndarray
            Matrix of partial derivatives.
        """
        deriv = np.zeros((1, 7 + self.num_vars,
                          len(self.inl[0].fluid.val) + 3))

        ######################################################################
        # derivatives for bus parameter of thermal input (TI)
        if bus.param == 'TI':
            for i in [2, 3, 6]:
                deriv[0, i, 0] = (
                        self.numeric_deriv(self.bus_func, 'm', i, bus=bus))
                deriv[0, i, 3:] = (
                        self.numeric_deriv(self.bus_func, 'fluid', i, bus=bus))

        ######################################################################
        # derivatives for bus parameter of power production (P)
        elif bus.param == 'P':
            for i in [2, 3, 6]:
                deriv[0, i, 0] = (
                        self.numeric_deriv(self.bus_func, 'm', i, bus=bus))
                deriv[0, i, 3:] = (
                        self.numeric_deriv(self.bus_func, 'fluid', i, bus=bus))

            # variable power
            if self.P.is_var:
                deriv[0, 7 + self.P.var_pos, 0] = (
                        self.numeric_deriv(self.bus_func, 'P', 7, bus=bus))

        ######################################################################
        # derivatives for bus parameter of total heat production (Q)
        elif bus.param == 'Q':
            for i in range(2):
                deriv[0, i, 0] = (
                        self.numeric_deriv(self.bus_func, 'm', i, bus=bus))
                deriv[0, i, 2] = (
                        self.numeric_deriv(self.bus_func, 'h', i, bus=bus))
                deriv[0, i + 4, 2] = (
                        self.numeric_deriv(self.bus_func, 'h', i + 4, bus=bus))

        ######################################################################
        # derivatives for bus parameter of heat production 1 (Q1)
        elif bus.param == 'Q1':
            deriv[0, 0, 0] = self.numeric_deriv(self.bus_func, 'm', 0, bus=bus)
            deriv[0, 0, 2] = self.numeric_deriv(self.bus_func, 'h', 0, bus=bus)
            deriv[0, 4, 2] = self.numeric_deriv(self.bus_func, 'h', 4, bus=bus)

        ######################################################################
        # derivatives for bus parameter of heat production 2 (Q2)
        elif bus.param == 'Q2':
            deriv[0, 1, 0] = self.numeric_deriv(self.bus_func, 'm', 1, bus=bus)
            deriv[0, 1, 2] = self.numeric_deriv(self.bus_func, 'h', 1, bus=bus)
            deriv[0, 5, 2] = self.numeric_deriv(self.bus_func, 'h', 5, bus=bus)

        ######################################################################
        # derivatives for bus parameter of heat loss (Qloss)
        elif bus.param == 'Qloss':
            for i in [2, 3, 6]:
                deriv[0, i, 0] = (
                        self.numeric_deriv(self.bus_func, 'm', i, bus=bus))
                deriv[0, i, 3:] = (
                        self.numeric_deriv(self.bus_func, 'fluid', i, bus=bus))

            # variable power
            if self.P.is_var:
                deriv[0, 7 + self.P.var_pos, 0] = (
                        self.numeric_deriv(self.bus_func, 'P', 7, bus=bus))

        ######################################################################
        # missing/invalid bus parameter
        else:
            msg = ('The parameter ' + str(bus.param) +
                   ' is not a valid parameter for a ' + self.component() + '.')
            logging.error(msg)
            raise ValueError(msg)

        return deriv

    def Q1_func(self):
        r"""
        Calculates residual value with specified Q1.

        Returns
        -------
        val : float
            Residual value of equation.

            .. math::

                val = \dot{m}_1 \cdot \left(h_{out,1} -
                h_{in,1} \right) - \dot{Q}_1
        """
        i = self.inl[0]
        o = self.outl[0]

        return self.Q1.val - i.m.val_SI * (o.h.val_SI - i.h.val_SI)

    def Q2_func(self):
        r"""
        Calculates residual value with specified Q2.

        Returns
        -------
        val : float
            Residual value of equation.

            .. math::

                0 = \dot{m}_2 \cdot \left(h_{out,2} - h_{in,2} \right) -
                \dot{Q}_2
        """
        i = self.inl[1]
        o = self.outl[1]

        return self.Q2.val - i.m.val_SI * (o.h.val_SI - i.h.val_SI)

    def tiP_char_func(self):
        r"""
        Calculates the relation of output power and thermal input from
        specified characteristic line.

        Returns
        -------
        val : float
            Residual value of equation.

            .. math::

                0 = P \cdot f_{TI}\left(\frac{P}{P_{ref}}\right)- LHV \cdot
                \left[\sum_i \left(\dot{m}_{in,i} \cdot
                x_{f,i}\right) - \dot{m}_{out,3} \cdot x_{f,3} \right]
                \; \forall i \in [1,2]
        """
        if np.isnan(self.P.design):
            expr = 1
        else:
            expr = self.P.val / self.P.design

        return self.calc_ti() - self.tiP_char.func.evaluate(expr) * self.P.val

    def Q1_char_func(self):
        r"""
        Calculates the relation of heat output 1 and thermal input from
        specified characteristic lines.

        Returns
        -------
        val : float
            Residual value of equation.

            .. math::

                \begin{split}
                0 = & \dot{m}_1 \cdot \left(h_{out,1} - h_{in,1} \right) \cdot
                f_{TI}\left(\frac{P}{P_{ref}}\right) \\
                & - LHV \cdot \left[\sum_i
                \left(\dot{m}_{in,i} \cdot x_{f,i}\right) -
                \dot{m}_{out,3} \cdot x_{f,3} \right] \cdot
                f_{Q1}\left(\frac{P}{P_{ref}}\right)\\
                \end{split}\\
                \forall i \in [3,4]
        """
        i = self.inl[0]
        o = self.outl[0]

        if np.isnan(self.P.design):
            expr = 1
        else:
            expr = self.P.val / self.P.design

        return (self.calc_ti() * self.Q1_char.func.evaluate(expr) -
                self.tiP_char.func.evaluate(expr) * i.m.val_SI *
                (o.h.val_SI - i.h.val_SI))

    def Q2_char_func(self):
        r"""
        Calculates the relation of heat output 2 and thermal input from
        specified characteristic lines.

        Returns
        -------
        val : float
            Residual value of equation.

            .. math::

                \begin{split}
                0 = & \dot{m}_2 \cdot \left(h_{out,2} - h_{in,2} \right) \cdot
                f_{TI}\left(\frac{P}{P_{ref}}\right) \\
                & - LHV \cdot \left[\sum_i
                \left(\dot{m}_{in,i} \cdot x_{f,i}\right) -
                \dot{m}_{out,3} \cdot x_{f,3} \right] \cdot
                f_{Q2}\left(\frac{P}{P_{ref}}\right)\\
                \end{split}\\
                \forall i \in [3,4]
        """
        i = self.inl[1]
        o = self.outl[1]

        if np.isnan(self.P.design):
            expr = 1
        else:
            expr = self.P.val / self.P.design

        return (self.calc_ti() * self.Q2_char.func.evaluate(expr) -
                self.tiP_char.func.evaluate(expr) * i.m.val_SI *
                (o.h.val_SI - i.h.val_SI))

    def Qloss_char_func(self):
        r"""
        Calculates the relation of heat loss and thermal input from
        specified characteristic lines.

        Returns
        -------
        val : float
            Residual value of equation.

            .. math::

                \begin{split}
                0 = & \dot{Q}_{loss} \cdot
                f_{TI}\left(\frac{P}{P_{ref}}\right) \\
                & - LHV \cdot \left[\sum_i
                \left(\dot{m}_{in,i} \cdot x_{f,i}\right) -
                \dot{m}_{out,3} \cdot x_{f,3} \right] \cdot
                f_{QLOSS}\left(\frac{P}{P_{ref}}\right)\\
                \end{split}\\
                \forall i \in [3,4]
        """
        if np.isnan(self.P.design):
            expr = 1
        else:
            expr = self.P.val / self.P.design

        return (self.calc_ti() * self.Qloss_char.func.evaluate(expr) -
                self.tiP_char.func.evaluate(expr) * self.Qloss.val)

    def calc_ti(self):
        r"""
        Calculates the thermal input of the combustion engine.

        Returns
        -------
        ti : float
            Thermal input.

            .. math::

                ti = LHV \cdot \left[\sum_i \left(\dot{m}_{in,i} \cdot x_{f,i}
                \right) - \dot{m}_{out,3} \cdot x_{f,3} \right]

                \forall i \in [3,4]
        """

        ti = 0
        for f in self.fuel_list:
            m = 0
            for i in self.inl[2:]:
                m += i.m.val_SI * i.fluid.val[f]

            for o in self.outl[2:]:
                m -= o.m.val_SI * o.fluid.val[f]

            ti += m * self.fuels[f]['LHV']

        return ti

    def calc_P(self):
        r"""
        Calculates the power output of the combustion engine.

        Returns
        -------
        P : float
            Power output.

            .. math::

                P = \frac{LHV \cdot \dot{m}_{f}}
                {f_{TI}\left(\frac{P}{P_{ref}}\right)}

        """
        if np.isnan(self.P.design):
            expr = 1
        else:
            expr = self.P.val / self.P.design

        return self.calc_ti() / self.tiP_char.func.evaluate(expr)

    def calc_Qloss(self):
        r"""
        Calculates the heat loss of the combustion engine.

        Returns
        -------
        Qloss : float
            Heat loss.

            .. math::

                \dot{Q}_{loss} = \frac{LHV \cdot \dot{m}_{f} \cdot
                f_{QLOSS}\left(\frac{P}{P_{ref}}\right)}
                {f_{TI}\left(\frac{P}{P_{ref}}\right)}
        """
        if np.isnan(self.P.design):
            expr = 1
        else:
            expr = self.P.val / self.P.design

        return (self.calc_ti() * self.Qloss_char.func.evaluate(expr) /
                self.tiP_char.func.evaluate(expr))

    def initialise_fluids(self, nw):
        r"""
        Calculate reaction balance for generic starting values at outlet.

        Parameters
        ----------
        nw : tespy.networks.network
            Network using this component object.
        """
        N_2 = 0.7655
        O_2 = 0.2345

        n_fuel = 1
        lamb = 3

        fact_fuel = {}
        sum_fuel = 0
        for f in self.fuel_list:
            fact_fuel[f] = 0
            for i in self.inl:
                fact_fuel[f] += i.fluid.val[f] / 2
            sum_fuel += fact_fuel[f]

        for f in self.fuel_list:
            fact_fuel[f] /= sum_fuel

        m_co2 = 0
        m_h2o = 0
        m_fuel = 0
        for f in self.fuel_list:
            m_co2 += (n_fuel * self.fuels[f]['C'] * molar_masses[self.co2] *
                      fact_fuel[f])
            m_h2o += (n_fuel * self.fuels[f]['H'] /
                      2 * molar_masses[self.h2o] * fact_fuel[f])
            m_fuel += n_fuel * molar_masses[f] * fact_fuel[f]

        n_o2 = (m_co2 / molar_masses[self.co2] +
                0.5 * m_h2o / molar_masses[self.h2o]) * lamb

        m_air = n_o2 * molar_masses[self.o2] / O_2
        m_fg = m_air + m_fuel

        m_o2 = n_o2 * molar_masses[self.o2] * (1 - 1 / lamb)
        m_n2 = N_2 * m_air

        fg = {
            self.n2: m_n2 / m_fg,
            self.co2: m_co2 / m_fg,
            self.o2: m_o2 / m_fg,
            self.h2o: m_h2o / m_fg
        }

        o = self.outl[2]
        for fluid, x in o.fluid.val.items():
            if not o.fluid.val_set[fluid] and fluid in fg.keys():
                o.fluid.val[fluid] = fg[fluid]

    @staticmethod
    def initialise_source(c, key):
        r"""
        Return a starting value for pressure and enthalpy at outlet.

        Parameters
        ----------
        c : tespy.connections.connection
            Connection to perform initialisation on.

        key : str
            Fluid property to retrieve.

        Returns
        -------
        val : float
            Starting value for pressure/enthalpy in SI units.

            .. math::

                val = \begin{cases}
                5 \cdot 10^5 & \text{key = 'p'}\\
                10^6 & \text{key = 'h'}
                \end{cases}
        """
        if key == 'p':
            return 5e5
        elif key == 'h':
            return 10e5

    @staticmethod
    def initialise_target(c, key):
        r"""
        Return a starting value for pressure and enthalpy at inlet.

        Parameters
        ----------
        c : tespy.connections.connection
            Connection to perform initialisation on.

        key : str
            Fluid property to retrieve.

        Returns
        -------
        val : float
            Starting value for pressure/enthalpy in SI units.

            .. math::

                val = \begin{cases}
                5 \cdot 10^5 & \text{key = 'p'}\\
                5 \cdot 10^5 & \text{key = 'h'}
                \end{cases}
        """
        if key == 'p':
            return 5e5
        elif key == 'h':
            return 5e5

    def calc_parameters(self):
        r"""
        Postprocessing parameter calculation.
        """
        i1 = self.inl[0].to_flow()
        i2 = self.inl[1].to_flow()
        o1 = self.outl[0].to_flow()
        o2 = self.outl[1].to_flow()

        v_i1 = v_mix_ph(i1, T0=self.inl[0].T.val_SI)
        v_o1 = v_mix_ph(o1, T0=self.outl[0].T.val_SI)
        v_i2 = v_mix_ph(i2, T0=self.inl[1].T.val_SI)
        v_o2 = v_mix_ph(o1, T0=self.outl[1].T.val_SI)

        self.pr1.val = o1[1] / i1[1]
        self.pr2.val = o2[1] / i2[1]
        self.zeta1.val = ((i1[1] - o1[1]) * np.pi ** 2 /
                          (8 * i1[0] ** 2 * (v_i1 + v_o1) / 2))
        self.zeta2.val = ((i2[1] - o2[1]) * np.pi ** 2 /
                          (8 * i2[0] ** 2 * (v_i2 + v_o2) / 2))
        self.Q1.val = i1[0] * (o1[2] - i1[2])
        self.Q2.val = i2[0] * (o2[2] - i2[2])
        self.P.val = self.calc_P()
        self.Qloss.val = self.calc_Qloss()

        # get bound errors for characteristic lines
        if np.isnan(self.P.design):
            expr = 1
        else:
            expr = self.P.val / self.P.design
        self.tiP_char.func.get_bound_errors(expr, self.label)
        self.Qloss_char.func.get_bound_errors(expr, self.label)
        self.Q1_char.func.get_bound_errors(expr, self.label)
        self.Q2_char.func.get_bound_errors(expr, self.label)

        combustion_chamber.calc_parameters(self)
