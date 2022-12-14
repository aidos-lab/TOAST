"""Command-line interface for Euclidicity calculations.

This script is the main command-line interface for our Euclidicity
calculations. It supports loading various input formats, for which
it will calculate Euclidicity scores.
"""

import argparse
import colorlog
import functools
import joblib
import os

import numpy as np
import pandas as pd

from toast.data import sample_vision_data_set
from toast.euclidicity import Euclidicity
from toast.shapes import sample_from_constant_curvature_annulus


def load(filename, batch_size, n_query_points):
    """Load data from filename, depending on input type.

    Parameters
    ----------
    filename : str
        If this points to a file name, the function will attempt to load
        said file and parse it. Else, the function will consider this as
        the name of a data set to load.

    batch_size : int
        Number of points to sample from data set.

    n_query_points : int
        Number of points to use for the subsequent Euclidicity
        calculations. It is possible to use the full data set.

    Returns
    -------
    Tuple of np.array, np.array
        The (subsampled) data set along with its query points is
        returned.
    """
    if os.path.exists(filename):
        ext = os.path.splitext(filename)[1]
        if ext == ".txt" or ext == ".gz":
            X = np.loadtxt(filename)
        elif ext == ".npz":
            X = np.load(filename)["data"]
    else:
        X = sample_vision_data_set(filename, batch_size)

    assert X is not None, RuntimeError(
        f"Unable to handle input file {filename}"
    )

    logger.info(f"Sampling a batch of {batch_size} points")
    logger.info(f"Using {n_query_points} query points")

    X = X[rng.choice(X.shape[0], batch_size, replace=False)]
    query_points = X[rng.choice(X.shape[0], n_query_points, replace=False)]

    return X, query_points


def estimate_scales(X, query_points, k_max):
    """Perform simple scale estimation of the data set.

    Parameters
    ----------
    k_max : int
        Maximum number of neighbours to consider for the local scale
        estimation.

    Returns
    --------
    List of dict
        A list of dictionaries consisting of the minimum and maximum
        inner and outer radius, respectively.
    """
    from sklearn.neighbors import KDTree

    tree = KDTree(X)
    distances, _ = tree.query(query_points, k=k_max, return_distance=True)

    # Ignore the distance to ourself, as we know that one already.
    distances = distances[:, 1:]

    scales = [
        {
            "r": dist[0],
            "R": dist[round(k_max / 3)],
            "s": dist[round(k_max / 3)],
            "S": dist[-1],
        }
        for dist in distances
    ]

    return scales


def setup():
    """Perform logging and argument parsing setup.

    Sets up the command-line interface for subsequent usage so that we
    do not clutter up the actual Euclidicity calculations.

    Returns
    -------
    Tuple of logger and parsed arguments
    """
    handler = colorlog.StreamHandler()
    handler.setFormatter(
        colorlog.ColoredFormatter("%(log_color)s%(levelname)-.1s: %(message)s")
    )

    logger = colorlog.getLogger()
    logger.addHandler(handler)
    logger.setLevel(colorlog.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "INPUT",
        type=str,
        help="Input point cloud or name of data set to load. If this points "
        "to an existing file, the file is loaded. Else the input is treated "
        "as the name of a (vision) data set.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Output file (optional). If not set, data will be printed to "
        "standard output. If set, will guess the output format based "
        "on the file extension.",
    )

    euclidicity_group = parser.add_argument_group("Euclidicity calculations")

    euclidicity_group.add_argument(
        "-k",
        "--num-neighbours",
        default=50,
        type=int,
        help="Number of neighbours for parameter estimation",
    )
    euclidicity_group.add_argument(
        "-d",
        "--dimension",
        default=2,
        type=int,
        help="Known or estimated intrinsic dimension",
    )
    euclidicity_group.add_argument(
        "-r",
        type=float,
        help="Minimum inner radius of annulus",
    )
    euclidicity_group.add_argument(
        "-R",
        type=float,
        help="Maximum inner radius of annulus",
    )
    euclidicity_group.add_argument(
        "-s",
        type=float,
        help="Minimum outer radius of annulus",
    )
    euclidicity_group.add_argument(
        "-S",
        type=float,
        help="Maximum outer radius of annulus",
    )
    euclidicity_group.add_argument(
        "--num-steps",
        default=10,
        type=int,
        help="Number of steps for annulus sampling",
    )

    sampling_group = parser.add_argument_group("Sampling")

    sampling_group.add_argument(
        "-b",
        "--batch-size",
        default=10000,
        type=int,
        help="Number of points to sample from input data",
    )
    sampling_group.add_argument(
        "-q",
        "--num-query-points",
        default=1000,
        type=int,
        help="Number of query points for Euclidicity calculations",
    )
    sampling_group.add_argument(
        "--seed",
        type=int,
        help="Random number generator seed for reproducible experiments",
    )

    experimental_group = parser.add_argument_group("Experimental")

    experimental_group.add_argument(
        "--curvature",
        "-K",
        type=float,
        default=None,
        help="If set, change model space from Euclidean annulus to 2D disk of "
        "constant curvature.",
    )

    args = parser.parse_args()
    return logger, args


if __name__ == "__main__":
    logger, args = setup()

    if args.seed is not None:
        logger.info(f"Using pre-defined seed {args.seed}")

    rng = np.random.default_rng(args.seed)

    X, query_points = load(args.INPUT, args.batch_size, args.num_query_points)

    r, R, s, S = args.r, args.R, args.s, args.S
    k = args.num_neighbours

    # Check whether we have to perform scale estimation on a per-point
    # basis. If not, we just supply an empty dict.
    if all([x is not None for x in [r, R, s, S]]):
        logger.info(
            f"Using global scales r = {r:.2f}, R = {R:.2f}, "
            f"s = {s:.2f}, S = {S:.2f}"
        )

        scales = [dict()] * len(query_points)
    else:
        logger.info(
            f"Performing scale estimation with k = {k} since no "
            f"parameters have been provided by the client."
        )

        scales = estimate_scales(X, query_points, k)

    max_dim = args.dimension
    n_steps = args.num_steps

    logger.info(f"Maximum dimension: {max_dim}")
    logger.info(f"Number of steps for local sampling: {n_steps}")

    model_sample_fn = None
    if args.curvature is not None:
        model_sample_fn = functools.partial(
            sample_from_constant_curvature_annulus, K=args.curvature
        )

    euclidicity = Euclidicity(
        max_dim=max_dim,
        n_steps=n_steps,
        r=args.r,
        R=args.R,
        s=args.s,
        S=args.S,
        method="ripser",
        data=X,
        model_sample_fn=model_sample_fn,
    )

    def _process(x, scale=None):
        scores, dimensions = euclidicity(X, x, **scale)

        # Aggregate over all scores that we find. We could pick
        # a different aggregation here!
        score = np.mean(np.nan_to_num(scores))
        dimension = np.mean(dimensions)

        return score, dimension

    output = joblib.Parallel(n_jobs=-1)(
        joblib.delayed(_process)(x, scale)
        for x, scale in zip(query_points, scales)
    )

    df = pd.DataFrame(
        output, columns=["euclidicity", "persistent_intrinsic_dimension"]
    )

    df = pd.concat([pd.DataFrame(query_points).add_prefix("X"), df], axis=1)

    if args.output is None:
        print(df.to_csv(index=False))
    else:
        extension = os.path.splitext(args.output)[1]
        if extension == ".tsv":
            df.to_csv(args.output, index=False, sep="\t")
        elif extension == ".csv":
            df.to_csv(args.output, index=False)
        elif extension == ".npy":
            np.save(args.output, df)
        elif extension == ".npz":
            np.savez(args.output, df)
