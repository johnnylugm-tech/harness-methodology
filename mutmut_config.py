def pre_mutation(context):
    """
    Hook called by mutmut before a mutation is applied.
    Allows us to skip specific lines to optimize ROI and reduce noise.
    """
    line = context.current_source_line.strip()
    
    # Skip any logger calls to prevent polluting test results with unverified string mutants
    if line.startswith("logger.") or " logging." in line or line.startswith("logging."):
        context.skip = True
