import click


def add_common_options(options):
    def add_options(func):
        for option in reversed(options):
            func = option(func)
        return func
    return add_options


COMMON_TRANSCRIBE_OPTIONS = [
    click.argument("input_audio", type=click.Path(exists=True)),
    click.option(
        "-m",
        "--model-path",
        help="Path to the pre-trained model for transcription",
        type=click.Path(exists=True),
    ),
    click.option(
        "-o",
        "--output",
        help="Path to output the prediction file (could be MIDI, CSV, ..., etc.)",
        default="./",
        show_default=True,
        type=click.Path(writable=True)
    )
]


COMMON_GEN_FEATURE_OPTIONS = [
    click.option(
        "-d",
        "--dataset-path",
        help="Path to the downloaded dataset",
        type=click.Path(exists=True),
        required=True
    ),
    click.option(
        "-o",
        "--output-path",
        help="Path for svaing the extracted feature. Default to the folder under the dataset.",
        type=click.Path(writable=True),
        default="+",
        show_default=True,
    ),
    click.option(
        "-n",
        "--num-threads",
        help="Parallel extract the feature by using multiple threads.",
        type=int,
        default=4,
        show_default=True
    )
]


COMMON_TRAIN_MODEL_OPTIONS = [
    click.option(
        "-d",
        "--feature-path",
        help="Path to the folder of extracted feature",
        type=click.Path(exists=True),
        required=True,
    ),
    click.option(
        "-m",
        "--model-name",
        help="Name for the output model (can be a path)",
        type=click.Path(writable=True)
    ),
    click.option(
        "-i",
        "--input-model",
        help="If given, the training will continue to fine-tune on the pre-trained model.",
        type=click.Path(exists=True, writable=True),
    ),
    click.option("-e", "--epochs", help="Number of training epochs", type=int, default=20),
    click.option("-s", "--steps", help="Number of training steps of each epoch", type=int, default=3000),
    click.option("-vs", "--val-steps", help="Number of validation steps of each epoch", type=int, default=500),
    click.option("-b", "--batch-size", help="Batch size of each training step", type=int, default=16),
    click.option("-vb", "--val-batch-size", help="Batch size of each validation step", type=int, default=16),
    click.option(
        "--early-stop",
        help="Stop the training after the given epoch number if the validation accuracy did not improve.",
        type=int,
        default=6,
    )
]