{smcl}
{* *! version 0.4.1}{...}
{vieweralsosee "python" "help python"}{...}
{viewerjumpto "Syntax" "taxsim_py##syntax"}{...}
{viewerjumpto "Description" "taxsim_py##description"}{...}
{viewerjumpto "Setup" "taxsim_py##setup"}{...}
{viewerjumpto "Using it" "taxsim_py##using"}{...}
{viewerjumpto "Options" "taxsim_py##options"}{...}
{viewerjumpto "Input variables" "taxsim_py##inputs"}{...}
{viewerjumpto "Output variables" "taxsim_py##outputs"}{...}
{viewerjumpto "Remarks" "taxsim_py##remarks"}{...}
{viewerjumpto "Examples" "taxsim_py##examples"}{...}
{viewerjumpto "Stored results" "taxsim_py##results"}{...}
{title:Title}

{phang}
{bf:taxsim_py} {hline 2} Federal, state and payroll taxes with the same inputs and outputs as NBER's {cmd:taxsim35}


{marker syntax}{...}
{title:Syntax}

{pstd}
Calculate taxes for the data in memory

{p 8 17 2}
{cmd:taxsim_py} [{cmd:,} {it:options}]

{pstd}
Create or update a Python environment for {cmd:taxsim_py}

{p 8 17 2}
{cmd:taxsim_py install}{cmd:,} {opt path(folder)} [{opt update} | {opt version(x.y.z)}] [{opt uv(path)}]

{synoptset 22 tabbed}{...}
{synopthdr}
{synoptline}
{syntab:Results}
{synopt:{opt r:eplace}}merge the results into the data in memory on {cmd:taxsimid}, replacing existing output variables{p_end}
{synopt:{opt sav:ing(filename)}}write the results to {it:filename}; its extension sets the file type{p_end}

{syntab:Detail and marginal rates}
{synopt:{opt f:ull}}also return the detailed federal and state worksheets, {cmd:v10}-{cmd:v45}{p_end}
{synopt:{opt s:econdary}}marginal rates with respect to the secondary earner's wages{p_end}
{synopt:{opt w:ages}}marginal rates with respect to both earners' wages{p_end}
{synopt:{opt i:nterest}}marginal rates with respect to interest income{p_end}
{synopt:{opt l:ong}}marginal rates with respect to long-term capital gains{p_end}
{synopt:{opt m:ortgage}}marginal rates with respect to mortgage interest{p_end}

{syntab:Calculation}
{synopt:{opt back:wards_compatible}}reproduce TAXSIM's own calculation, including its errors, instead of statutory law{p_end}
{synopt:{opt miss:ing_to_zero}}treat missing input values as 0 instead of stopping with an error{p_end}
{synopt:{opt thr:eads(#)}}number of threads; otherwise {cmd:POLARS_MAX_THREADS}, otherwise all cores{p_end}
{synoptline}
{p 4 6 2}
{cmd:taxsim_py} requires Stata 16 or newer.{p_end}


{marker description}{...}
{title:Description}

{pstd}
{cmd:taxsim_py} calculates US federal and state income taxes and payroll taxes for every
observation in memory. It reads the same input variables as NBER's {cmd:taxsim35} and
{cmd:taxsimlocal35} and returns at least the same output variables, with the same names and
labels, so existing do-files need little more than the command name changed.

{pstd}
The calculation is done by the {browse "https://pypi.org/project/taxsim-py/":taxsim-py}
Python package, run in Stata's own Python. Dollar amounts are not rounded on the way in or
out.

{pstd}
By default {cmd:taxsim_py} calculates under current statutory law, correcting the places
where TAXSIM departs from it; the {browse "https://jrothbaum.github.io/taxsim_py/statutory_corrections/":corrections}
are listed in the documentation. Use {opt backwards_compatible} to reproduce TAXSIM itself.

{pstd}
Results are kept only when asked for: {opt replace} merges them into the data in memory,
{opt saving()} writes them to a file, and either or both may be given. With neither,
{cmd:taxsim_py} stops before calculating. Unlike {cmd:taxsim35}, nothing is written to the
current directory by default.


{marker setup}{...}
{title:Setup}

{pstd}
{cmd:taxsim_py} has two parts: this Stata command, and a Python environment containing the
{browse "https://pypi.org/project/taxsim-py/":taxsim-py} package, which does the calculation
inside Stata's own Python ({help python}). You set up the environment once. It never uses
{cmd:shell}, so it works the same in batch mode.

{phang}
{bf:Step 1. Make sure Stata has a Python.} Stata needs a Python 3.9 or newer installation. Type
{cmd:python query}; if it shows none, install Python (for example with
{browse "https://docs.astral.sh/uv/":uv}, or from {browse "https://www.python.org":python.org})
and point Stata at it as described in {help python}. On Windows this must be an installed
Python, not a virtual environment's {cmd:python.exe}.

{phang}
{bf:Step 2. Put taxsim-py in a Python environment.} Choose one:

{phang2}
{bf:(a) Let taxsim_py create one.} This needs {browse "https://docs.astral.sh/uv/":uv}
installed. Run {cmd:taxsim_py install, path(}{it:folder}{cmd:)} with a folder that does not
exist yet (or is empty). It builds an environment there and installs taxsim-py into it. The
environment can be used at once, in the same session, with no restart.

{phang2}
{bf:(b) Use an environment you already have.} Install taxsim-py into it with
{cmd:uv add "taxsim-py[readstat]"}. The
{cmd:[readstat]} part is required: it is how data pass between Stata and taxsim-py.

{phang}
{bf:Step 3. Make Stata use it in later sessions.} {cmd:taxsim_py install} prints a
{cmd:set python_userpath} line (and, if Stata's Python is not the one the environment was built
on, a {cmd:set python_exec} line as well). Run the lines it prints at the start of a session,
or put them in your {cmd:profile.do}.
For an environment you made yourself, {cmd:taxsim_py install, path(}{it:folder}{cmd:)} changes
nothing and prints the same lines to use.

{pstd}
Before it calculates, {cmd:taxsim_py} checks that all of this is in place. When something is
missing it stops and prints the setting to change: a Python Stata cannot start (for a virtual
environment, the Python it was built on), taxsim-py or polars-readstat not importable, or a
taxsim-py older than this command needs.

{pstd}
{cmd:taxsim_py install} only changes environments it created. Pointed at any other
environment, it stops and prints the lines to use that environment instead. To upgrade, run it
again with {opt update}; to install a specific release, with {opt version()}.

{marker using}{...}
{title:Using it}

{pstd}
Load your data and run {cmd:taxsim_py}. It reads the {help taxsim_py##inputs:input variables} in
memory, calculates taxes for every observation, and returns the
{help taxsim_py##outputs:output variables}. You must say where the results go:
{opt replace} merges them into the data in memory, {opt saving()} writes them to a file, or both.

{phang2}{cmd:. use households, clear}{p_end}
{phang2}{cmd:. taxsim_py, replace}{p_end}
{phang2}{cmd:. summarize fiitax siitax fica}{p_end}

{pstd}
Add {opt full} for the detailed worksheets, or {opt backwards_compatible} to reproduce
{cmd:taxsim35}'s own results instead of statutory law. See {help taxsim_py##options:Options} and
{help taxsim_py##examples:Examples}.

{marker options}{...}
{title:Options}

{dlgtab:Results}

{phang}
{opt replace} merges the results into the data in memory on {cmd:taxsimid}, replacing
output variables that already exist. If the data have no {cmd:taxsimid}, it is created as
{cmd:_n}. Without {opt replace}, the data in memory are left unchanged.

{phang}
{opt saving(filename [, replace])} writes the results to {it:filename}. The extension sets
the type: {cmd:.dta} (with variable labels), {cmd:.parquet}, {cmd:.csv}, {cmd:.tsv},
{cmd:.arrow}, {cmd:.ndjson} or {cmd:.sav}. An existing file is overwritten only with
{cmd:replace}.

{dlgtab:Detail and marginal rates}

{phang}
{opt full} also returns TAXSIM's detailed federal and state worksheets as {cmd:v10}-{cmd:v45}.

{phang}
{opt secondary}, {opt wages}, {opt interest}, {opt long} and {opt mortgage} choose the
income or deduction the marginal rates {cmd:frate} and {cmd:srate} are calculated for, as
in {cmd:taxsim35}. The default is the primary earner's wages.

{dlgtab:Calculation}

{phang}
{opt backwards_compatible} reproduces TAXSIM's own calculation, including its simplifications
and errors, and is slower (it runs the Python engine). The default is statutory law, which
follows the law where TAXSIM is wrong and uses the compiled engine unless {opt full} is given.

{phang}
{opt missing_to_zero} treats missing input values as 0. Without it, a missing value in an
input variable is an error, as in {cmd:taxsim35}.

{phang}
{opt threads(#)} sets the number of threads. Python's thread pool is fixed once taxsim-py
first runs in a Stata session, so {opt threads()} takes effect only on the first call;
afterwards, set {cmd:POLARS_MAX_THREADS} before starting Stata.

{dlgtab:taxsim_py install}

{phang}
{opt path(folder)} is where the environment is created. The folder must not exist yet, be
empty, or hold an environment {cmd:taxsim_py install} created earlier.

{phang}
{opt update} upgrades taxsim-py to the newest release. {opt version(x.y.z)} installs that
release instead. If this session has already loaded taxsim-py, restart Stata to use the
new version.

{phang}
{opt uv(path)} gives the location of uv when it is not on the search path or in uv's
default install folder.


{marker inputs}{...}
{title:Input variables}

{pstd}
The same names as {cmd:taxsim35}. Only {cmd:year} and {cmd:mstat} are required; absent dollar
amounts default to 0, taxpayer ages to 50, and dependents to under 13.

{p 8 8 2}
{cmd:taxsimid year state mstat page sage depx dep6 dep13 dep17 dep18 dep19 age1 age2 age3}
{cmd:pwages swages psemp ssemp dividends intrec stcg ltcg otherprop nonprop pensions gssi ui}
{cmd:pui sui transfers rentpaid proptax otheritem childcare mortgage scorp pbusinc sbusinc}
{cmd:pprofinc sprofinc}

{pstd}
Input variables must be numeric, except that string values which convert to numbers without
loss are accepted. {cmd:taxsimid} only identifies observations, so it may be numeric or a
string; with {opt replace} it must be unique and never missing. See the {browse "https://jrothbaum.github.io/taxsim_py/inputs/":input documentation}
for definitions.


{marker outputs}{...}
{title:Output variables}

{p 8 8 2}
{cmd:taxsimid year state fiitax siitax fica frate srate ficar tfica}

{pstd}
With {opt full}, also {cmd:v10}-{cmd:v45}. All carry {cmd:taxsim35}'s variable labels. See the
{browse "https://jrothbaum.github.io/taxsim_py/outputs/":output documentation}.


{marker remarks}{...}
{title:Remarks}

{pstd}
Starting Python costs about a second on the first call in a session and little on later calls.

{pstd}
Results can differ from {cmd:taxsim35} for high incomes, because {cmd:taxsim35} rounds
values when it writes and reads its CSV files and {cmd:taxsim_py} does not.


{marker examples}{...}
{title:Examples}

{pstd}
Set up once, if you do not already have Python with taxsim-py (needs uv){p_end}
{phang2}{cmd:. taxsim_py install, path(}{it:folder}{cmd:)}{p_end}
{pstd}
then calculate at once; in later sessions, run the {cmd:set python_userpath} line it prints first.{p_end}

{pstd}
NBER's test case: a married couple with $100,000 of long-term capital gains in 1970{p_end}
{phang2}{cmd:. clear}{p_end}
{phang2}{cmd:. input state year mstat ltcg}{p_end}
{phang2}{cmd:  0 1970 2 100000}{p_end}
{phang2}{cmd:  end}{p_end}
{phang2}{cmd:. taxsim_py, full interest replace}{p_end}
{phang2}{cmd:. display fiitax}{p_end}

{pstd}
Keep the results in a file instead of merging them{p_end}
{phang2}{cmd:. taxsim_py, saving(taxes.dta)}{p_end}


{marker results}{...}
{title:Stored results}

{pstd}
{cmd:taxsim_py} stores the following in {cmd:r()}:

{synoptset 20 tabbed}{...}
{p2col 5 20 24 2: Scalars}{p_end}
{synopt:{cmd:r(N)}}number of observations calculated{p_end}
{p2col 5 20 24 2: Macros}{p_end}
{synopt:{cmd:r(version)}}the taxsim-py version that calculated them{p_end}
{synopt:{cmd:r(saving)}}the {opt saving()} file, if any{p_end}
{p2colreset}{...}

{pstd}
{cmd:taxsim_py install} stores the following in {cmd:r()}:

{synoptset 15 tabbed}{...}
{p2col 5 15 19 2: Macros}{p_end}
{synopt:{cmd:r(python)}}the environment's Python{p_end}
{synopt:{cmd:r(version)}}the installed taxsim-py version{p_end}
{synopt:{cmd:r(action)}}{cmd:created}, {cmd:installed}, {cmd:updated}, {cmd:version} or {cmd:none}{p_end}
{p2colreset}{...}


{title:Author}

{pstd}
Jon Rothbaum{break}
{browse "https://github.com/jrothbaum/taxsim_py"}


{title:Also see}

{pstd}
{browse "https://jrothbaum.github.io/taxsim_py/stata/":using taxsim-py from Stata};
{browse "https://jrothbaum.github.io/taxsim_py/":taxsim-py documentation};
{browse "https://taxsim.nber.org/taxsim35/":NBER TAXSIM 35}
{p_end}
