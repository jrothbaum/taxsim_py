{smcl}
{* *! version 0.2.0}{...}
{vieweralsosee "python" "help python"}{...}
{viewerjumpto "Syntax" "taxsim_py##syntax"}{...}
{viewerjumpto "Description" "taxsim_py##description"}{...}
{viewerjumpto "Setup" "taxsim_py##setup"}{...}
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
{synopt:{opt stat:utory}}calculate under statutory law rather than in TAXSIM-compatible mode{p_end}
{synopt:{opt miss:ing_to_zero}}treat missing input values as 0 instead of stopping with an error{p_end}
{synopt:{opt thr:eads(#)}}number of threads; otherwise {cmd:POLARS_MAX_THREADS}, otherwise all cores{p_end}
{synopt:{opt d:ebug}}keep the intermediate files and show where they are{p_end}
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
Python package, run in Stata's own Python. Data are passed to it through temporary
{cmd:.dta} files at double precision, so dollar amounts are not rounded on the way in or
out.

{pstd}
Results are kept only when asked for: {opt replace} merges them into the data in memory,
{opt saving()} writes them to a file, and either or both may be given. With neither,
{cmd:taxsim_py} stops before calculating. Unlike {cmd:taxsim35}, nothing is written to the
current directory by default.


{marker setup}{...}
{title:Setup}

{pstd}
{cmd:taxsim_py} needs Stata's Python ({help python}) to be able to import taxsim-py. There
are two ways to set that up.

{phang}
1. {bf:If you already use Python,} add {cmd:taxsim-py[readstat]} to the environment you
normally use (for example {cmd:uv add "taxsim-py[readstat]"} or
{cmd:pip install "taxsim-py[readstat]"}), and point Stata's Python at it with
{cmd:set python_exec}.

{phang}
2. {bf:If you do not use Python,} let {cmd:taxsim_py install} create an environment. It
needs {browse "https://docs.astral.sh/uv/":uv}, which downloads Python itself and needs no
administrator rights. It then prints the {cmd:set python_exec} line to run; add
{cmd:, permanently} to keep the setting for later sessions.

{pstd}
On Windows, Stata cannot load a virtual environment's {cmd:python.exe}. If Stata's Python
does not start, {cmd:taxsim_py} prints the lines to use instead: the environment's base
Python for {cmd:set python_exec}, and its packages for {cmd:set python_userpath}. Restart
Stata after changing these settings.

{pstd}
{cmd:taxsim_py install} only changes environments it created. Pointed at any other
environment, it stops and prints how to use that environment instead.


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
{opt statutory} calculates under statutory law. The default reproduces TAXSIM's own
calculation, including its simplifications.

{phang}
{opt missing_to_zero} treats missing input values as 0. Without it, a missing value in an
input variable is an error, as in {cmd:taxsim35}.

{phang}
{opt threads(#)} sets the number of threads. Python's thread pool is fixed once taxsim-py
first runs in a Stata session, so {opt threads()} takes effect only on the first call;
afterwards, set {cmd:POLARS_MAX_THREADS} before starting Stata.

{phang}
{opt debug} keeps the temporary input and output files and shows where they are.

{dlgtab:taxsim_py install}

{phang}
{opt path(folder)} is where the environment is created. The folder must not exist yet, be
empty, or hold an environment {cmd:taxsim_py install} created earlier.

{phang}
{opt update} upgrades taxsim-py to the newest release. {opt version(x.y.z)} installs that
release instead. Restart Stata afterwards if its Python is already running.

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
taxsim-py runs inside Stata's Python, so starting it costs about a second on the first
call in a session and little on later calls. Two consequences of sharing Stata's process:
a newer taxsim-py installed while Stata is running is used only after Stata restarts, and a
failure inside Python (for example running out of memory) can end the Stata session.

{pstd}
Results can differ from {cmd:taxsim35} for high incomes, because {cmd:taxsim35} rounds
values when it writes and reads its CSV files and {cmd:taxsim_py} does not.


{marker examples}{...}
{title:Examples}

{pstd}
Set up once, if you do not already have Python with taxsim-py{p_end}
{phang2}{cmd:. taxsim_py install, path(C:/taxsim_py_env)}{p_end}
{pstd}
then run the {cmd:set python_exec} line it prints, adding {cmd:, permanently}.{p_end}

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
{synopt:{cmd:r(debug_input)}}with {opt debug}, the inputs sent to taxsim-py{p_end}
{synopt:{cmd:r(debug_results)}}with {opt debug} and {opt replace}, the results merged back{p_end}
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
{browse "https://jrothbaum.github.io/taxsim_py/":taxsim-py documentation};
{browse "https://taxsim.nber.org/taxsim35/":NBER TAXSIM 35}
{p_end}
