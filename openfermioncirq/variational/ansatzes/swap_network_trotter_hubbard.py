#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""A variational ansatz based on a linear swap network Trotter step."""

from typing import Iterable, Optional, Sequence, Tuple, cast

import numpy
import sympy

import cirq

from openfermioncirq import swap_network
from openfermioncirq.variational.ansatz import VariationalAnsatz
from openfermioncirq.variational.letter_with_subscripts import (
        LetterWithSubscripts)


class SwapNetworkTrotterHubbardAnsatz(VariationalAnsatz):
    """A Hubbard model ansatz based on the fermionic swap network Trotter step.

    Each Trotter step includes 3 parameters: one for the horizontal hopping
    terms, one for the vertical hopping terms, and one for the on-site
    interaction. This ansatz is similar to the one used in arXiv:1507.08969,
    but corresponds to a different ordering for simulating the Hamiltonian
    terms.
    """

    def __init__(self,
                 x_dim: float,
                 y_dim: float,
                 tunneling: float,
                 coulomb: float,
                 periodic: bool=True,
                 iterations: int=1,
                 adiabatic_evolution_time: Optional[float]=None,
                 qubits: Optional[Sequence[cirq.Qid]]=None
                 ) -> None:
        """
        Args:
            iterations: The number of iterations of the basic template to
                include in the circuit. The number of parameters grows linearly
                with this value.
            adiabatic_evolution_time: The time scale for Hamiltonian evolution
                used to determine the default initial parameters of the ansatz.
                This is the value A from the docstring of this class.
                If not specified, defaults to the sum of the absolute values
                of the entries of the two-body tensor of the Hamiltonian.
            qubits: Qubits to be used by the ansatz circuit. If not specified,
                then qubits will automatically be generated by the
                `_generate_qubits` method.
        """
        self.x_dim = x_dim
        self.y_dim = y_dim
        self.tunneling = tunneling
        self.coulomb = coulomb
        self.periodic = periodic
        self.iterations = iterations

        if adiabatic_evolution_time is None:
            adiabatic_evolution_time = 0.1*abs(coulomb)*iterations
        self.adiabatic_evolution_time = cast(float, adiabatic_evolution_time)

        super().__init__(qubits)

    def params(self) -> Iterable[sympy.Symbol]:
        """The parameters of the ansatz."""
        for i in range(self.iterations):
            if self.x_dim > 1:
                yield LetterWithSubscripts('Th', i)
            if self.y_dim > 1:
                yield LetterWithSubscripts('Tv', i)
            yield LetterWithSubscripts('V', i)

    def param_bounds(self) -> Optional[Sequence[Tuple[float, float]]]:
        """Bounds on the parameters."""
        bounds = []
        for param in self.params():
            s = 1.0 if param.letter == 'V' else 2.0
            bounds.append((-s, s))
        return bounds

    def _generate_qubits(self) -> Sequence[cirq.Qid]:
        """Produce qubits that can be used by the ansatz circuit."""
        n_qubits = 2*self.x_dim*self.y_dim
        return cirq.LineQubit.range(n_qubits)

    def operations(self, qubits: Sequence[cirq.Qid]) -> cirq.OP_TREE:
        """Produce the operations of the ansatz circuit."""

        for i in range(self.iterations):

            # Apply one- and two-body interactions with a swap network that
            # reverses the order of the modes
            def one_and_two_body_interaction(p, q, a, b) -> cirq.OP_TREE:
                th_symbol = LetterWithSubscripts('Th', i)
                tv_symbol = LetterWithSubscripts('Tv', i)
                v_symbol = LetterWithSubscripts('V', i)
                if _is_horizontal_edge(
                        p, q, self.x_dim, self.y_dim, self.periodic):
                    yield cirq.ISwapPowGate(exponent=-th_symbol).on(a, b)
                if _is_vertical_edge(
                        p, q, self.x_dim, self.y_dim, self.periodic):
                    yield cirq.ISwapPowGate(exponent=-tv_symbol).on(a, b)
                if _are_same_site_opposite_spin(p, q, self.x_dim*self.y_dim):
                    yield cirq.CZPowGate(exponent=v_symbol).on(a, b)
            yield swap_network(
                    qubits, one_and_two_body_interaction, fermionic=True)
            qubits = qubits[::-1]

            # Apply one- and two-body interactions again. This time, reorder
            # them so that the entire iteration is symmetric
            def one_and_two_body_interaction_reversed_order(p, q, a, b
                    ) -> cirq.OP_TREE:
                th_symbol = LetterWithSubscripts('Th', i)
                tv_symbol = LetterWithSubscripts('Tv', i)
                v_symbol = LetterWithSubscripts('V', i)
                if _are_same_site_opposite_spin(p, q, self.x_dim*self.y_dim):
                    yield cirq.CZPowGate(exponent=v_symbol).on(a, b)
                if _is_vertical_edge(
                        p, q, self.x_dim, self.y_dim, self.periodic):
                    yield cirq.ISwapPowGate(exponent=-tv_symbol).on(a, b)
                if _is_horizontal_edge(
                        p, q, self.x_dim, self.y_dim, self.periodic):
                    yield cirq.ISwapPowGate(exponent=-th_symbol).on(a, b)
            yield swap_network(
                    qubits, one_and_two_body_interaction_reversed_order,
                    fermionic=True, offset=True)
            qubits = qubits[::-1]

    def default_initial_params(self) -> numpy.ndarray:
        """Approximate evolution by H(t) = T + (t/A)V.

        Sets the parameters so that the ansatz circuit consists of a sequence
        of second-order Trotter steps approximating the dynamics of the
        time-dependent Hamiltonian H(t) = T + (t/A)V, where T is the one-body
        term and V is the two-body term of the Hamiltonian used to generate the
        ansatz circuit, and t ranges from 0 to A, where A is equal to
        `self.adibatic_evolution_time`. The number of Trotter steps
        is equal to the number of iterations in the ansatz. This choice is
        motivated by the idea of state preparation via adiabatic evolution.

        The dynamics of H(t) are approximated as follows. First, the total
        evolution time of A is split into segments of length A / r, where r
        is the number of Trotter steps. Then, each Trotter step simulates H(t)
        for a time length of A / r, where t is the midpoint of the
        corresponding time segment. As an example, suppose A is 100 and the
        ansatz has two iterations. Then the approximation is achieved with two
        Trotter steps. The first Trotter step simulates H(25) for a time length
        of 50, and the second Trotter step simulates H(75) for a time length
        of 50.
        """

        total_time = self.adiabatic_evolution_time
        step_time = total_time / self.iterations

        params = []
        for param, scale_factor in zip(self.params(),
                                       self.param_scale_factors()):
            if param.letter == 'Th' or param.letter == 'Tv':
                params.append(_canonicalize_exponent(
                    -self.tunneling * step_time / numpy.pi, 4) / scale_factor)
            elif param.letter == 'V':
                i, = param.subscripts
                # Use the midpoint of the time segment
                interpolation_progress = 0.5 * (2 * i + 1) / self.iterations
                params.append(_canonicalize_exponent(
                    -0.5 * self.coulomb * interpolation_progress *
                    step_time / numpy.pi, 2) / scale_factor)

        return numpy.array(params)


def _is_horizontal_edge(p, q, x_dim, y_dim, periodic):
    n_sites = x_dim*y_dim
    if p < n_sites and q >= n_sites or q < n_sites and p >= n_sites:
        return False
    if p >= n_sites and q >= n_sites:
        p -= n_sites
        q -= n_sites
    return (q == _right_neighbor(p, x_dim, y_dim, periodic)
            or p == _right_neighbor(q, x_dim, y_dim, periodic))


def _is_vertical_edge(p, q, x_dim, y_dim, periodic):
    n_sites = x_dim*y_dim
    if p < n_sites and q >= n_sites or q < n_sites and p >= n_sites:
        return False
    if p >= n_sites and q >= n_sites:
        p -= n_sites
        q -= n_sites
    return (q == _bottom_neighbor(p, x_dim, y_dim, periodic)
            or p == _bottom_neighbor(q, x_dim, y_dim, periodic))


def _are_same_site_opposite_spin(p, q, n_sites):
    return abs(p-q) == n_sites


def _right_neighbor(site, x_dimension, y_dimension, periodic):
    if x_dimension == 1:
        return None
    if (site + 1) % x_dimension == 0:
        if periodic:
            return site + 1 - x_dimension
        else:
            return None
    return site + 1


def _bottom_neighbor(site, x_dimension, y_dimension, periodic):
    if y_dimension == 1:
        return None
    if site + x_dimension + 1 > x_dimension*y_dimension:
        if periodic:
            return site + x_dimension - x_dimension*y_dimension
        else:
            return None
    return site + x_dimension


def _canonicalize_exponent(exponent: float, period: int) -> float:
    # Shift into [-p/2, +p/2).
    exponent += period / 2
    exponent %= period
    exponent -= period / 2
    # Prefer (-p/2, +p/2] over [-p/2, +p/2).
    if exponent <= -period / 2:
        exponent += period  # coverage: ignore
    return exponent
