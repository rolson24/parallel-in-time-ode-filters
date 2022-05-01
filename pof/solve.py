from functools import partial

import jax
import jax.numpy as jnp
import matplotlib.pyplot as plt
import parsmooth
import tornadox
import diffrax

from parsmooth import MVNSqrt, FunctionalModel
from parsmooth.linearization import extended, cubature

# from parsmooth.parallel import ekf
# from parsmooth.sequential._filtering import filtering as seq_ekf


def diffrax_solve(ivp, ts, rtol=1e-3, atol=1e-3, max_steps=int(1e6)):
    vector_field = lambda t, y, args: ivp.f(t, y)
    term = diffrax.ODETerm(vector_field)
    solver = diffrax.Dopri5()
    # saveat = diffrax.SaveAt(steps=True)
    saveat = diffrax.SaveAt(t1=True, ts=ts)
    stepsize_controller = diffrax.PIDController(rtol=rtol, atol=atol)

    sol = diffrax.diffeqsolve(
        term,
        solver,
        t0=ivp.t0,
        t1=ivp.tmax,
        dt0=None,
        y0=ivp.y0,
        saveat=saveat,
        stepsize_controller=stepsize_controller,
        max_steps=max_steps,
    )
    idxs = jnp.isfinite(sol.ts)
    ts = sol.ts[idxs]
    ys = sol.ys[idxs]
    return ts, ys


def solve(
    f,
    y0,
    T,
    order=3,
    dt=1e-2,
    diffusion=0.1,
    method="ekf",
    n_iter="auto",
):

    d, q = y0.shape[0], order
    D = d * (q + 1)

    iwp = tornadox.iwp.IntegratedWienerTransition(
        num_derivatives=q, wiener_process_dimension=d
    )
    P, PI = iwp.nordsieck_preconditioner(dt)
    E0 = iwp.projection_matrix(0) @ P
    E1 = iwp.projection_matrix(1) @ P
    A, QL = iwp.preconditioned_discretize

    b = jnp.zeros(D)
    transition_model = FunctionalModel(lambda x: A @ x, MVNSqrt(b, QL))

    c = jnp.zeros(d)
    RS = 0 * jnp.eye(d)
    observation_model = FunctionalModel(
        lambda x: E1 @ x - f(None, E0 @ x), MVNSqrt(c, RS)
    )

    times = jnp.arange(0, T + dt, dt)
    data = jnp.zeros((len(times) - 1, d))
    N = len(times)

    m0, P0 = tornadox.init.TaylorMode()(f=f, df=None, y0=y0, t0=0, num_derivatives=q)
    m0, P0 = jnp.concatenate(m0.T), jnp.kron(jnp.eye(d), P0)
    m0, P0 = PI @ m0, PI @ P0 @ PI
    x0 = MVNSqrt(m0, P0)

    kwargs = {
        "observations": data,
        "x0": x0,
        "transition_model": transition_model,
        "observation_model": observation_model,
        "linearization_method": extended,
    }
    if method.lower() == "ekf":
        res = parsmooth.filtering(
            **kwargs,
            nominal_trajectory=None,
            parallel=False,
        )
    elif method.lower() == "eks":
        res = parsmooth.filter_smoother(
            **kwargs,
            nominal_trajectory=None,
            parallel=False,
        )
    elif method.lower() == "ieks":
        init_lin = MVNSqrt(
            jnp.repeat(x0.mean.reshape(1, -1), data.shape[0] + 1, axis=0),
            jnp.zeros((data.shape[0] + 1, d * (order + 1), d * (order + 1))),
        )
        if n_iter == "auto":
            criterion = mse_criterion
        else:
            criterion = lambda i, *args: i < n_iter

        res = parsmooth.iterated_smoothing(
            **kwargs,
            init_nominal_trajectory=init_lin,
            parallel=False,
            criterion=criterion,
        )
    else:
        raise ValueError(f"The specified method {method} is not supported.")

    return times, jnp.dot(E0, res.mean.T).T


def mse_criterion(i, prev_traj, curr_traj, tol=1e-6):
    return jnp.mean((pref_traj.mean - curr_traj.mean) ** 2) > 1e-6


def get_solver_iterator(
    ivp,
    order=3,
    dt=1e-2,
    diffusion=0.1,
    parallel=True,
):

    d, q = ivp.y0.shape[0], order
    D = d * (q + 1)

    iwp = tornadox.iwp.IntegratedWienerTransition(
        num_derivatives=q, wiener_process_dimension=d
    )
    P, PI = iwp.nordsieck_preconditioner(dt)
    E0 = iwp.projection_matrix(0) @ P
    E1 = iwp.projection_matrix(1) @ P
    A, QL = iwp.preconditioned_discretize

    b = jnp.zeros(D)
    transition_model = FunctionalModel(lambda x: A @ x, MVNSqrt(b, QL))

    c = jnp.zeros(d)
    RS = 0 * jnp.eye(d)
    observation_model = FunctionalModel(
        lambda x: E1 @ x - ivp.f(None, E0 @ x), MVNSqrt(c, RS)
    )

    times = jnp.arange(ivp.t0, ivp.tmax + dt, dt)
    data = jnp.zeros((len(times) - 1, d))
    N = len(times)

    m0, P0 = tornadox.init.TaylorMode()(
        f=ivp.f, df=None, y0=ivp.y0, t0=0, num_derivatives=q
    )
    m0, P0 = jnp.concatenate(m0.T), jnp.kron(jnp.eye(d), P0)
    m0, P0 = PI @ m0, PI @ P0 @ PI
    x0 = MVNSqrt(m0, P0)

    kwargs = {
        "observations": data,
        "x0": x0,
        "transition_model": transition_model,
        "observation_model": observation_model,
        "linearization_method": extended,
    }

    init_lin = MVNSqrt(
        jnp.repeat(x0.mean.reshape(1, -1), data.shape[0] + 1, axis=0),
        jnp.zeros((data.shape[0] + 1, d * (order + 1), d * (order + 1))),
    )

    # @jax.jit
    def refine(trajectory):
        return parsmooth.filter_smoother(
            **kwargs,
            nominal_trajectory=trajectory,
            parallel=parallel,
        )

    def project(trajectory):
        return jnp.dot(E0, trajectory.mean.T).T

    return init_lin, refine, project
