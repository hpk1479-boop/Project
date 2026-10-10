from .contracts import NativeCell,EMPTY_VALUE
from .numeric import cell,add,sub,mul,div,power,sqrt,sequential_sum,controlled,taints

class SequentialPopulationDispersion:
    @staticmethod
    def calculate(values):
        values=tuple(values);mean=div(sequential_sum(values),len(values))
        return mean,SequentialPopulationDispersion.around_mean(values,mean)
    @staticmethod
    def around_mean(values,mean):
        values=tuple(values);variance=cell(0.0)
        for value in values:variance=add(variance,power(sub(value,mean),2))
        return sqrt(div(variance,len(values)))

def basis_color(now,previous):
    if not now.available or not previous.available:return NativeCell(None,'SOURCE_WRITTEN',taints(now,previous))
    return controlled(cell(0 if now.value>previous.value else 1 if now.value<previous.value else 2),now,previous)

class OscillatorRegimeStage:
    @staticmethod
    def row(state,i,R,period,alpha):
        read=lambda name,j:state.read(name,j)
        window=tuple(read('smooth',i+j) for j in range(period))
        if R-i==period:
            state.write('basis',i,div(sequential_sum(window),period),'basis')
            state.write('color',i,2,'basis')
        elif R-i>period:
            previous=read('basis',i+1)
            basis=add(mul(alpha,read('smooth',i)),mul(1.0-alpha,previous))
            state.write('basis',i,basis,'basis');state.write('color',i,basis_color(basis,previous),'basis')
        _,std=SequentialPopulationDispersion.calculate(window)
        state.write('regime_upper',i,add(read('basis',i),mul(std,0.4)),'regime')
        state.write('regime_lower',i,sub(read('basis',i),mul(std,0.4)),'regime')

class PriceRegimeStage:
    @staticmethod
    def row(state,source,i,R,P,limit):
        if i+19>=R:
            state.write('basis',i,EMPTY_VALUE,'regime');state.write('color',i,2,'regime')
            state.write('regime_upper',i,EMPTY_VALUE,'regime');state.write('regime_lower',i,EMPTY_VALUE,'regime');return
        window=tuple(source(i+j) for j in range(20));mean=div(sequential_sum(window),20)
        need_seed=P==0 and i==limit
        previous=state.read('basis',i+1)
        if not need_seed and i+1<R:
            if not previous.available:
                unknown=NativeCell(None,'SOURCE_WRITTEN',taints(previous,mean,source(i)))
                for name in ('basis','color','regime_upper','regime_lower'):state.write(name,i,unknown,'regime')
                return
            need_seed=previous.value==EMPTY_VALUE or previous.value==0.0
        if need_seed or i+1>=R:
            basis=mean;color=cell(2)
            if not(P==0 and i==limit) and i+1<R:
                basis=controlled(basis,previous);color=controlled(color,previous)
        else:
            alpha=2.0/21.0;basis=add(mul(alpha,source(i)),mul(1.0-alpha,previous));color=basis_color(basis,previous)
        state.write('basis',i,basis,'regime');state.write('color',i,color,'regime')
        std=SequentialPopulationDispersion.around_mean(window,mean)
        state.write('regime_upper',i,add(basis,mul(std,0.4)),'regime')
        state.write('regime_lower',i,sub(basis,mul(std,0.4)),'regime')
