from libstempo.libstempo import *
import libstempo as T
import psrchive
import numpy as np
import matplotlib.pyplot as plt
import PTMCMCSampler
from PTMCMCSampler import PTMCMCSampler as ptmcmc
import scipy as sp
import corner

class Likelihood(object):
    
	def __init__(self):
	
		
		self.SECDAY = 24*60*60

		self.parfile = None
		self.timfile = None
		self.psr = None  
		self.SatSecs = None
		self.SatDays = None
		self.FNames = None
		self.NToAs = None
		self.numTime = None	   
		self.TempoPriors = None

		self.ProfileData= None
		self.ProfileMJDs= None
		self.ProfileInfo= None

		self.toas= None
		self.residuals =  None
		self.BatCorrs =  None
		self.ModelBats =  None

		self.designMatrix = None
		self.FisherU = None

		self.TScrunched = None
		self.TScrunchedNoise = None

		self.Nbins = None
		self.ShiftedBinTimes = None
		self.ReferencePeriod = None
		self.ProfileStartBats = None
		self.ProfileEndBats = None

		self.MaxCoeff = None
		self.MLShapeCoeff = None
		self.MeanBeta = None
		self.MeanPhase = None

		self.doplot = None
	
		self.parameters = None
		self.pmin = None
		self.pmax = None
		self.startPoint = None
		self.cov_diag = None
		self.hess = None

		self.InterpolatedTime = None
		self.InterpBasis = None

		self.getShapeletStepSize = False
		




	def loadPulsar(self, parfile, timfile):
		self.psr = T.tempopulsar(parfile=parfile, timfile = timfile)    
		self.psr.fit()
		self.SatSecs = self.psr.satSec()
		self.SatDays = self.psr.satDay()
		self.FNames = self.psr.fnames()
		self.NToAs = self.psr.nobs
		    


		#Check how many timing model parameters we are fitting for (in addition to phase)
		self.numTime=len(self.psr.pars())
		redChisq = self.psr.chisq()/(self.psr.nobs-len(self.psr.pars())-1)
		self.TempoPriors=np.zeros([self.numTime,2]).astype(np.float128)
		for i in range(self.numTime):
			self.TempoPriors[i][0]=self.psr[self.psr.pars()[i]].val
			self.TempoPriors[i][1]=self.psr[self.psr.pars()[i]].err/np.sqrt(redChisq)
			print "fitting for: ", self.psr.pars()[i], self.TempoPriors[i][0], self.TempoPriors[i][1]

		#Now loop through archives, and work out what subint/frequency channel is associated with a ToA.
		#Store whatever meta data is needed (MJD of the bins etc)
		#If multiple polarisations are present we first PScrunch.

		self.ProfileData=[]
		self.ProfileMJDs=[]
		self.ProfileInfo=[]


		profcount = 0
		while(profcount < self.NToAs):
		    arch=psrchive.Archive_load(self.FNames[profcount])

		    
		    npol = arch.get_npol()
		    if(npol>1):
			arch.pscrunch()

		    nsub=arch.get_nsubint()


		    for i in range(nsub):
			subint=arch.get_Integration(i)
		
			nbins = subint.get_nbin()
			nchans = subint.get_nchan()
			npols = subint.get_npol()
			foldingperiod = subint.get_folding_period()
			inttime = subint.get_duration()
			centerfreq = subint.get_centre_frequency()
		
			print "Subint Info:", i, nbins, nchans, npols, foldingperiod, inttime, centerfreq
		
			firstbin = subint.get_epoch()
			intday = firstbin.intday()
			fracday = firstbin.fracday()
			intsec = firstbin.get_secs()
			fracsecs = firstbin.get_fracsec()
			isdedispersed = subint.get_dedispersed()
		
			pulsesamplerate = foldingperiod/nbins/self.SECDAY;
		
			nfreq=subint.get_nchan()
		
			FirstBinSec = intsec + np.float128(fracsecs)
			SubIntTimeDiff = FirstBinSec-self.SatSecs[profcount]*self.SECDAY
			PeriodDiff = SubIntTimeDiff*self.psr['F0'].val
		
			if(abs(PeriodDiff) < 2.0):
			    for j in range(nfreq):
				chanfreq = subint.get_centre_frequency(j)
				toafreq = self.psr.freqs[profcount]
				prof=subint.get_Profile(0,j)
				profamps = prof.get_amps()
				
				if(np.sum(profamps) != 0 and abs(toafreq-chanfreq) < 0.001):
				    noiselevel=self.GetProfNoise(profamps)
				    self.ProfileData.append(np.copy(profamps))

				    self.ProfileInfo.append([self.SatSecs[profcount], self.SatDays[profcount], np.float128(intsec)+np.float128(fracsecs), pulsesamplerate, nbins, foldingperiod, noiselevel])                    
				    print "ChanInfo:", j, chanfreq, toafreq, np.sum(profamps)
				    profcount += 1
				    if(profcount == self.NToAs):
				        break



		self.ProfileInfo=np.array(self.ProfileInfo)
		self.ProfileData=np.array(self.ProfileData)

		self.toas=self.psr.toas()
		self.residuals = self.psr.residuals(removemean=False)
		self.BatCorrs = self.psr.batCorrs()
		self.ModelBats = self.psr.satSec() + self.BatCorrs - self.residuals/self.SECDAY



		#get design matrix for linear timing model, setup jump proposal

		self.designMatrix=self.psr.designmatrix(incoffset=False)
		for i in range(self.numTime):
			self.designMatrix[:,i] *= self.TempoPriors[i][1]
			zval = self.designMatrix[0,i]
			self.designMatrix[:,i] -= zval

		self.designMatrix=np.float64(self.designMatrix)
		N=np.diag(1.0/(self.psr.toaerrs*10.0**-6))
		Fisher=np.dot(self.designMatrix.T, np.dot(N, self.designMatrix))
		FisherU,FisherS,FisherVT=np.linalg.svd(Fisher)

		self.FisherU = FisherU

		self.ProfileStartBats = self.ProfileInfo[:,2]/self.SECDAY + self.ProfileInfo[:,3]*0 + self.ProfileInfo[:,3]*0.5 + self.BatCorrs
		self.ProfileEndBats =  self.ProfileInfo[:,2]/self.SECDAY + self.ProfileInfo[:,3]*(self.ProfileInfo[:,4]-1) + self.ProfileInfo[:,3]*0.5 + self.BatCorrs

		self.Nbins = self.ProfileInfo[:,4]
		ProfileBinTimes = []
		for i in range(self.NToAs):
			ProfileBinTimes.append((np.linspace(self.ProfileStartBats[i], self.ProfileEndBats[i], self.Nbins[i])- self.ModelBats[i])*self.SECDAY)
		self.ShiftedBinTimes = np.float64(np.array(ProfileBinTimes))

		self.ReferencePeriod = np.float64(self.ProfileInfo[0][5])



	
	#Funtion to determine an estimate of the white noise in the profile data
	def GetProfNoise(self, profamps):

		Nbins = len(profamps)
		Step=100
		noiselist=[]
		for i in range(Nbins-Step):
			noise=np.std(profamps[i:i+Step])
			noiselist.append(noise)
		noiselist=np.array(noiselist)
		minnoise=np.min(noiselist)
		threesiglist=noiselist[noiselist<3*minnoise]
		mediannoise=np.median(threesiglist)
		return mediannoise



	def TScrunch(self, doplot=True):

		TScrunched = np.zeros(1024)
		totalweight = 0

		profcount = 0
		while(profcount < self.NToAs):
		    arch=psrchive.Archive_load(self.FNames[profcount])

		    
		    npol = arch.get_npol()
		    if(npol>1):
			arch.pscrunch()

		    arch.centre()
		    arch.remove_baseline()

		    nsub=arch.get_nsubint()


		    for i in range(nsub):
			subint=arch.get_Integration(i)
		
			nbins = subint.get_nbin()
			nchans = subint.get_nchan()
			npols = subint.get_npol()
			foldingperiod = subint.get_folding_period()
			inttime = subint.get_duration()
			centerfreq = subint.get_centre_frequency()
		
		
			firstbin = subint.get_epoch()
			intday = firstbin.intday()
			fracday = firstbin.fracday()
			intsec = firstbin.get_secs()
			fracsecs = firstbin.get_fracsec()
			isdedispersed = subint.get_dedispersed()
		
			pulsesamplerate = foldingperiod/nbins/self.SECDAY;
		
			nfreq=subint.get_nchan()
		
			FirstBinSec = intsec + np.float128(fracsecs)
			SubIntTimeDiff = FirstBinSec-self.SatSecs[profcount]*self.SECDAY
			PeriodDiff = SubIntTimeDiff*self.psr['F0'].val
		
			if(abs(PeriodDiff) < 2.0):
			    for j in range(nfreq):
				chanfreq = subint.get_centre_frequency(j)
				toafreq = self.psr.freqs[profcount]
				prof=subint.get_Profile(0,j)
				profamps = prof.get_amps()
				
				if(np.sum(profamps) != 0 and abs(toafreq-chanfreq) < 0.001):
				    noiselevel=self.GetProfNoise(profamps)
				    weight = 1.0/noiselevel**2
				    totalweight += weight

				    TScrunched += profamps*weight

				    profcount += 1
				    if(profcount == self.NToAs):
				        break

		TScrunched /= totalweight

		if(doplot == True):
			plt.plot(np.linspace(0,1,1024), TScrunched)
			plt.show()	

		self.TScrunched = TScrunched
		self.TScrunchedNoise = self.GetProfNoise(TScrunched)



	def my_prior(self, x):
	    logp = 0.

	    if np.all(x <= self.pmax) and np.all(x >= self.pmin):
		logp = np.sum(np.log(1/(self.pmax-self.pmin)))
	    else:
		logp = -np.inf

	    return logp



	def getInitialParams(self, MaxCoeff = 1, parameters = ['Phase', 'Width'], pmin = [0,0.01], pmax = [1, 1], x0 = [0.5, 0.1], cov_diag = [0.1, 0.1], burnin = 1000, outDir = './Initchains/'):
	

		print "Getting initial fit to profile using averaged data, fitting for: ", parameters

		n_params = len(parameters)

		    
		self.pmin = np.array(pmin)
		self.pmax = np.array(pmax)
		self.MaxCoeff = MaxCoeff

		x0 = np.array(x0)
		cov_diag = np.array(cov_diag)


		self.doplot = False
		sampler = ptmcmc.PTSampler(ndim=n_params,logl=self.InitialLogLike,logp=self.my_prior,
				            cov=np.diag(cov_diag**2),
				            outDir=outDir,
				            resume=True)

		sampler.sample(p0=x0,Niter=10000,isave=10,burn=burnin,thin=1,neff=1000)

		chains=np.loadtxt('./Initchains/chain_1.txt').T
		ML=chains.T[np.argmax(chains[-3][burnin:])][:n_params]
		self.doplot=True
		self.MLShapeCoeff = self.InitialLogLike(ML)
		self.MeanBeta = ML[1]

	def getInitialPhase(self, parameters = ['Phase'], pmin = [-1.0], pmax = [1.0], x0 = [0.0], cov_diag = [0.1], burnin = 1000, outDir = './PhaseChains/'):
	

		print "Getting initial fit to profile using averaged data, fitting for: ", parameters

		n_params = len(parameters)

		    
		self.pmin = np.array(pmin)
		self.pmax = np.array(pmax)

		x0 = np.array(x0)
		cov_diag = np.array(cov_diag)


		self.doplot = False
		sampler = ptmcmc.PTSampler(ndim=n_params,logl=self.PhaseLike,logp=self.my_prior,
				            cov=np.diag(cov_diag**2),
				            outDir=outDir,
				            resume=True)

		sampler.sample(p0=x0,Niter=5000,isave=10,burn=burnin,thin=1,neff=1000)

		chains=np.loadtxt('./PhaseChains/chain_1.txt').T
		ML=chains.T[np.argmax(chains[-3][burnin:])][:n_params]
		self.MeanPhase = ML[0]

		self.getShapeletStepSize = True
		self.hess = self.PhaseLike(ML)
		self.getShapeletStepSize = False

		#self.doplot=True
		#self.PhaseLike(ML)
		

	

	#@profile
	def InitialLogLike(self, x):
	    

		pcount = 0
		phase = x[pcount]
		pcount += 1
		width = x[pcount]
		pcount += 1


		loglike = 0


		'''Start by working out position in phase of the model arrival time'''


		x = (np.linspace(0, 1, len(self.TScrunched)) - phase)/width

		hermiteMatrix = np.zeros([len(self.TScrunched), self.MaxCoeff])
		for i in range(self.MaxCoeff):
			amps = np.zeros(self.MaxCoeff)
			amps[i] = 1
			s = numpy.polynomial.hermite.hermval(x, amps)*np.exp(-0.5*(x)**2)
			s/=np.std(s)
			hermiteMatrix[:,i] = s

		MTM = np.dot(hermiteMatrix.T, hermiteMatrix)

		Prior = 1000.0
		diag=MTM.diagonal().copy()
		diag += 1.0/Prior**2
		np.fill_diagonal(MTM, diag)

		Md = np.dot(hermiteMatrix.T, self.TScrunched)
		Chol_MTM = sp.linalg.cho_factor(MTM)
		ML = sp.linalg.cho_solve(Chol_MTM, Md)

		s = np.dot(hermiteMatrix, ML)

		r = self.TScrunched - s	


		loglike  = -0.5*np.sum(r**2)/self.TScrunchedNoise**2

		if(self.doplot == True):

		    plt.plot(np.linspace(0,1,len(self.TScrunched)), self.TScrunched)
		    plt.plot(np.linspace(0,1,len(self.TScrunched)),s)
		    plt.show()
		    plt.plot(np.linspace(0,1,len(self.TScrunched)),self.TScrunched-s)
		    plt.show()

		    zml = ML[0]
		    return ML/zml

		return loglike



	#Function returns matrix containing interpolated shapelet basis vectors given a time 'interpTime' in ns, and a Beta value to use.
	def PreComputeShapelets(self, interpTime = 1, MeanBeta = 0.1):


		print("Calculating Shapelet Interpolation Matrix : ", interpTime, MeanBeta);

		'''
		/////////////////////////////////////////////////////////////////////////////////////////////  
		/////////////////////////Profile Params//////////////////////////////////////////////////////
		/////////////////////////////////////////////////////////////////////////////////////////////
		'''

		InterpBins = np.max(self.Nbins)

		numtointerpolate = np.int(self.ReferencePeriod/InterpBins/interpTime/10.0**-9)+1
		InterpolatedTime = self.ReferencePeriod/InterpBins/numtointerpolate

		InterpShapeMatrix = []
		MeanBeta = MeanBeta*self.ReferencePeriod

		
		interpStep = self.ReferencePeriod/InterpBins/numtointerpolate
	
	

		for t in range(numtointerpolate):


			binpos = t*interpStep

			samplerate = self.ReferencePeriod/InterpBins
			x = np.linspace(binpos, binpos+samplerate*(InterpBins-1), InterpBins)
			x = ( x + self.ReferencePeriod/2) % (self.ReferencePeriod ) - self.ReferencePeriod/2
			x=x/MeanBeta

			hermiteMatrix = np.zeros([InterpBins,self.MaxCoeff])
			for i in range(self.MaxCoeff):
				amps = np.zeros(self.MaxCoeff)
				amps[i] = 1
				s = numpy.polynomial.hermite.hermval(x, amps)*np.exp(-0.5*(x)**2)
				s/=np.std(s)
				hermiteMatrix[:,i] = s
			InterpShapeMatrix.append(np.copy(hermiteMatrix))

	
		InterpShapeMatrix = np.array(InterpShapeMatrix)
		print("Finished Computing Interpolated Profiles")
		self.InterpBasis = InterpShapeMatrix
		self.InterpolatedTime  = InterpolatedTime



	#@profile
	def PhaseLike(self, x):
	    

		pcount = 0
		phase = x[pcount]*self.ReferencePeriod
		pcount += 1

		loglike = 0

		stepsize=np.zeros(self.MaxCoeff - 1)


		for i in range(self.NToAs):

			'''Start by working out position in phase of the model arrival time'''


			x = self.ShiftedBinTimes[i]-phase
			x[0] = ( x[0] + self.ReferencePeriod/2) % (self.ReferencePeriod ) - self.ReferencePeriod/2

			InterpBin = np.int(x[0]%(self.ReferencePeriod/self.Nbins[i])/self.InterpolatedTime)
			WBT = x[0]-self.InterpolatedTime*InterpBin
			RollBins=np.int(np.round(WBT/(self.ReferencePeriod/self.Nbins[i])))

	
			#Evaulate Shapelet model: to be replaced with interpolated matrix

			s = np.roll(np.dot(self.InterpBasis[InterpBin], self.MLShapeCoeff), -RollBins)

	
			#Now subtract mean and scale so std is one.  Makes the matrix stuff stable.

			smean = np.sum(s)/self.Nbins[i] 
			s = s-smean

			sstd = np.dot(s,s)/self.Nbins[i]
			s=s/np.sqrt(sstd)

			#Make design matrix.  Two components: baseline and profile shape.

			M=np.ones([2,self.Nbins[i]])
			M[1] = s


			pnoise = self.ProfileInfo[i][6]

			MNM = np.dot(M, M.T)      
			MNM /= (pnoise*pnoise)

			#Invert design matrix. 2x2 so just do it numerically


			detMNM = MNM[0][0]*MNM[1][1] - MNM[1][0]*MNM[0][1]
			InvMNM = np.zeros([2,2])
			InvMNM[0][0] = MNM[1][1]/detMNM
			InvMNM[1][1] = MNM[0][0]/detMNM
			InvMNM[0][1] = -1*MNM[0][1]/detMNM
			InvMNM[1][0] = -1*MNM[1][0]/detMNM

			logdetMNM = np.log(detMNM)
			    
			#Now get dNM and solve for likelihood.
			    
			    
			dNM = np.dot(self.ProfileData[i], M.T)/(pnoise*pnoise)


			dNMMNM = np.dot(dNM.T, InvMNM)
			MarginLike = np.dot(dNMMNM, dNM)

			profilelike = -0.5*(logdetMNM - MarginLike)
			loglike += profilelike


			if(self.getShapeletStepSize == True):
				amp = dNMMNM[1]
				for j in range(self.MaxCoeff - 1):
					BVec = amp*np.roll(self.InterpBasis[InterpBin][:,j], -RollBins)
					stepsize[j] += np.dot(BVec, BVec)/self.ProfileInfo[i][6]/self.ProfileInfo[i][6]


			if(self.doplot == True):
			    baseline=dNMMNM[0]
			    amp = dNMMNM[1]
			    noise = np.std(self.ProfileData[i] - baseline - amp*s)
			    print i, amp, baseline, noise
			    plt.plot(np.linspace(0,1,self.Nbins[i]), self.ProfileData[i])
			    plt.plot(np.linspace(0,1,self.Nbins[i]),baseline+s*amp)
			    plt.show()
			    plt.plot(np.linspace(0,1,self.Nbins[i]),self.ProfileData[i]-(baseline+s*amp))
			    plt.show()

		if(self.getShapeletStepSize == True):
			for j in range(self.MaxCoeff - 1):
				print "step size ", j,  stepsize[j], 1.0/np.sqrt(stepsize[j])
			return 1.0/np.sqrt(stepsize)
	
		return loglike


	#@profile

	def MarginLogLike(self, x):
	    

		pcount = 0
		phase=self.MeanPhase*self.ReferencePeriod
		pcount += 1

		NCoeff = self.MaxCoeff-1
		pcount += 1


		ShapeAmps=np.zeros(self.MaxCoeff)
		ShapeAmps[0] = 1
		ShapeAmps[1:] = x[pcount:pcount+(self.MaxCoeff-1)]
		pcount += self.MaxCoeff-1

		TimingParameters=x[pcount:pcount+self.numTime]
		pcount += self.numTime

		loglike = 0

		TimeSignal = np.dot(self.designMatrix, TimingParameters)

		xS = self.ShiftedBinTimes[:,0]-phase-TimeSignal
		xS = ( xS + self.ReferencePeriod/2) % (self.ReferencePeriod ) - self.ReferencePeriod/2

		InterpBins = (xS%(self.ReferencePeriod/self.Nbins[:])/self.InterpolatedTime).astype(int)
		WBTs = xS-self.InterpolatedTime*InterpBins
		RollBins=(np.round(WBTs/(self.ReferencePeriod/self.Nbins[:]))).astype(np.int)


		#Multiply and shift out the shapelet model

		s=[np.roll(np.dot(self.InterpBasis[InterpBins[i]][:,:NCoeff+1], ShapeAmps[:NCoeff+1]), -RollBins[i]) for i in range(len(RollBins))]

		#Subtract mean and rescale


		s = [s[i] - np.sum(s[i])/self.Nbins[i] for i in range(self.NToAs)]	
		s = [s[i]/(np.dot(s[i],s[i])/self.Nbins[i]) for i in range(self.NToAs)]


		for i in range(self.NToAs):


			'''Make design matrix.  Two components: baseline and profile shape.'''

			M=np.ones([2,self.Nbins[i]])
			M[1] = s[i]


			pnoise = self.ProfileInfo[i][6]

			MNM = np.dot(M, M.T)      
			MNM /= (pnoise*pnoise)

			'''Invert design matrix. 2x2 so just do it numerically'''


			detMNM = MNM[0][0]*MNM[1][1] - MNM[1][0]*MNM[0][1]
			InvMNM = np.zeros([2,2])
			InvMNM[0][0] = MNM[1][1]/detMNM
			InvMNM[1][1] = MNM[0][0]/detMNM
			InvMNM[0][1] = -1*MNM[0][1]/detMNM
			InvMNM[1][0] = -1*MNM[1][0]/detMNM

			logdetMNM = np.log(detMNM)
			    
			'''Now get dNM and solve for likelihood.'''
			    
			    
			dNM = np.dot(self.ProfileData[i], M.T)/(pnoise*pnoise)


			dNMMNM = np.dot(dNM.T, InvMNM)
			MarginLike = np.dot(dNMMNM, dNM)

			profilelike = -0.5*(logdetMNM - MarginLike)
			loglike += profilelike

			if(self.doplot == True):
			    baseline=dNMMNM[0]
			    amp = dNMMNM[1]
			    noise = np.std(self.ProfileData[i] - baseline - amp*s)
			    print i, amp, baseline, noise
			    plt.plot(np.linspace(0,1,self.Nbins[i]), self.ProfileData[i])
			    plt.plot(np.linspace(0,1,self.Nbins[i]),baseline+s[i]*amp)
			    plt.show()
			    plt.plot(np.linspace(0,1,self.Nbins[i]),self.ProfileData[i]-(baseline+s[i]*amp))
			    plt.show()

		return loglike


	#Jump proposal for the timing model parameters
	def TimeJump(self, x, iteration, beta):


		q=x.copy()
		y=np.dot(self.FisherU.T,x[-self.numTime:])
		ind = np.unique(np.random.randint(0,self.numTime,np.random.randint(0,self.numTime,1)[0]))
		ran=np.random.standard_normal(self.numTime)
		y[ind]=y[ind]+ran[ind]#/np.sqrt(FisherS[ind])

		newpars=np.dot(self.FisherU, y)
		q[-self.numTime:]=newpars

	
		return q, 0


'''

	def drawFromShapeletPrior(parameters, iter, beta):
	    
		# post-jump parameters
		q = parameters.copy()

		# transition probability
		qxy = 0

		# choose one coefficient at random to prior-draw on
		ind = np.unique(np.random.randint(1, MaxCoeff, 1))

		# where in your parameter list do the coefficients start?
		ct = 2

		for ii in ind:
		    
		    q[ct+ii] = np.random.uniform(pmin[ct+ii], pmax[ct+ii])
		    qxy += 0
	    
		return q, qxy
'''

