import jax
import jax.numpy as jnp
import tornadox

from pof.utils import MVNSqrt


def taylor_mode_init(f, y0, num_derivatives):
    d = y0.shape[0]
    m0, P0 = tornadox.init.TaylorMode()(
        f=f, df=None, y0=y0, t0=0, num_derivatives=num_derivatives
    )
    m0, P0 = jnp.concatenate(m0.T), jnp.kron(jnp.eye(d), P0)
    x0 = MVNSqrt(m0, P0)
    return x0


def uncertain_init(f, y0, num_derivatives, var=1):
    d = y0.shape[0]
    q = num_derivatives

    y0 = y0.reshape(-1, 1)
    dy0 = f(None, y0).reshape(-1, 1)

    m0 = jnp.concatenate([y0, dy0, jnp.zeros((d, (q - 1)))], axis=1)
    m0 = m0.reshape(-1)
    P0 = jnp.eye(d * (q + 1)) * var
    for j in range(d):
        P0 = P0.at[(q + 1) * j, (q + 1) * j].set(0)
        P0 = P0.at[(q + 1) * j + 1, (q + 1) * j + 1].set(0)
    x0 = MVNSqrt(m0, jnp.sqrt(P0))
    return x0


def get_initial_trajectory(y0, f, order, N=None, with_dy=True):
    d = y0.shape[-1]
    if len(y0.shape) == 1:
        assert N is not None
        # Single initial value => constant initial trajectory
        if with_dy:
            dy0 = f(None, y0)
        else:
            dy0 = jnp.zeros_like(y0)
        x0 = jnp.concatenate(
            [y0[:, None], dy0[:, None], jnp.zeros((d, (order - 1)))], axis=1
        )
        x0 = x0.reshape(1, -1)
        traj = jnp.repeat(x0, N, axis=0)
    else:
        assert N is None
        N = y0.shape[0]
        if with_dy:
            dy0 = jax.vmap(f, in_axes=(None, 0))(None, y0)
        else:
            dy0 = jnp.zeros_like(y0)
        traj = jnp.concatenate(
            [y0[:, :, None], dy0[:, :, None], jnp.zeros((N, d, (order - 1)))], axis=2
        )
        traj = traj.reshape(N, -1)
    return traj
