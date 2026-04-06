import click


@click.group()
def cli():
    """hashbidder CLI."""


@cli.command()
def hello():
    """Say hello."""
    click.echo("Hello from hashbidder!")


if __name__ == "__main__":
    cli()
